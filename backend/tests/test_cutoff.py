"""A model that stops because of its length limit is always noticed, on every provider and call type (Phase 1),
and a chat answer that hits the limit is continued. Simulated models only: no quota is used."""

import contextlib
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import llm  # noqa: E402
from completeness import Tally  # noqa: E402
from pydantic import BaseModel  # noqa: E402

NS = types.SimpleNamespace
GROQ = llm.Settings(provider="groq", model="m", groq_api_key="x")
OLLAMA = llm.Settings(provider="ollama", model="m")
CLAUDE = llm.Settings(provider="anthropic", model="claude-x")


class Item(BaseModel):
    a: int


@contextlib.contextmanager
def patched(obj, **values):
    old = {k: getattr(obj, k) for k in values}
    for k, v in values.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


def raises(exc_type, fn):
    try:
        fn()
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__}")


# ---------------------------------------------------------------- fakes

def groq_resp(content, finish="stop", tool_calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls), finish_reason=finish)],
              usage=NS(total_tokens=10, completion_tokens=5))


def tool_call(name="lookup", args="{}"):
    return NS(id="c1", function=NS(name=name, arguments=args))


class Script:
    """Stands in for a model call: returns the queued responses in order and remembers the messages it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        messages = args[1] if len(args) > 1 else kwargs["messages"]
        self.calls.append([dict(m) if isinstance(m, dict) else m for m in messages])
        return self.responses.pop(0)


def groq_loop(script, max_rounds=4, on_token=None, execute=lambda n, i: "result"):
    with patched(llm, _groq_chat=script):
        return llm._groq_tool_loop(GROQ, "sys", [{"role": "user", "content": "q"}], [], execute, max_rounds, on_token)


# ---------------------------------------------------------------- Groq

def test_groq_structured_cut_off_is_a_cut_off_error():
    with patched(llm, _groq_chat=Script(groq_resp('{"a": ', finish="length"))):
        exc = raises(llm.LLMCutOffError, lambda: llm._groq_structured(GROQ, "s", "u", Item, 100))
    assert exc.partial == '{"a": '


def test_groq_text_cut_off_is_not_returned_as_if_complete():
    with patched(llm, _groq_chat=Script(groq_resp("The mappings are BKPF -> FI_", finish="length"))):
        exc = raises(llm.LLMCutOffError, lambda: llm._groq_text(GROQ, "", "u", 100))
    assert exc.partial.startswith("The mappings")


def test_groq_text_complete_answer_is_returned():
    with patched(llm, _groq_chat=Script(groq_resp(" done "))):
        assert llm._groq_text(GROQ, "", "u", 100) == "done"


def test_groq_chat_answer_cut_off_is_continued():
    script = Script(groq_resp("Hello wor", "length"), groq_resp("ld, done.", "stop"))
    assert groq_loop(script) == "Hello world, done."
    second = script.calls[1]
    assert second[-2] == {"role": "assistant", "content": "Hello wor"}
    assert second[-1] == {"role": "user", "content": llm.CONTINUE_PROMPT}


def test_continuing_does_not_use_up_a_tool_round():
    script = Script(groq_resp("Hello wor", "length"), groq_resp("ld.", "stop"))
    assert groq_loop(script, max_rounds=1) == "Hello world."


def test_chat_answer_still_cut_after_two_continuations_says_so():
    tokens = []
    script = Script(groq_resp("a", "length"), groq_resp("b", "length"), groq_resp("c", "length"))
    reply = groq_loop(script, on_token=tokens.append)
    assert reply == "abc\n\n" + llm.CUT_OFF_NOTE
    assert len(script.calls) == 3                       # the first request plus two continuations, no more
    assert "".join(tokens).endswith(llm.CUT_OFF_NOTE)   # the streamed text says it too


def test_chat_tool_request_cut_off_is_an_error_not_a_guess():
    script = Script(groq_resp("", "length", tool_calls=[tool_call(args='{"table_na')]))
    raises(llm.LLMCutOffError, lambda: groq_loop(script))


def test_chat_tool_round_then_answer_still_works_and_rounds_are_limited():
    seen = []
    script = Script(groq_resp("", "tool_calls", [tool_call()]), groq_resp("Final.", "stop"))
    assert groq_loop(script, execute=lambda n, i: seen.append(n) or "r") == "Final." and seen == ["lookup"]
    endless = Script(*[groq_resp("", "tool_calls", [tool_call()]) for _ in range(5)])
    assert groq_loop(endless, max_rounds=3) is None
    assert len(endless.calls) == 3


def test_text_written_before_a_tool_call_is_kept_when_streaming():
    tokens = []
    script = Script(groq_resp("Let me check.", "tool_calls", [tool_call()]), groq_resp("Found it.", "stop"))
    assert groq_loop(script, on_token=tokens.append) == "Let me check.\n\nFound it."
    assert tokens == ["\n\n"]       # (the pieces themselves are streamed by the model call)


# ---------------------------------------------------------------- Ollama

def ollama_resp(content, done_reason="stop", tool_calls=None):
    return {"message": {"content": content, "tool_calls": tool_calls}, "done_reason": done_reason}


def test_ollama_structured_and_text_cut_off():
    with patched(llm, _ollama_chat=lambda *a, **k: ollama_resp('{"a":', "length")):
        raises(llm.LLMCutOffError, lambda: llm._ollama_structured(OLLAMA, "s", "u", Item, 100))
        raises(llm.LLMCutOffError, lambda: llm._ollama_text(OLLAMA, "", "u", 100))
    with patched(llm, _ollama_chat=lambda *a, **k: ollama_resp("fine")):
        assert llm._ollama_text(OLLAMA, "", "u", 100) == "fine"


def test_ollama_chat_answer_cut_off_is_continued_and_think_blocks_are_hidden():
    script = Script(ollama_resp("<think>x</think>Hello wor", "length"), ollama_resp("ld.", "stop"))
    with patched(llm, _ollama_chat=script, _ollama_model_info=lambda m: None):
        reply = llm._ollama_tool_loop(OLLAMA, "sys", [{"role": "user", "content": "q"}], [], lambda n, i: "", 4)
    assert reply == "Hello world."
    assert script.calls[1][-1]["content"] == llm.CONTINUE_PROMPT


# ---------------------------------------------------------------- Claude

def claude_msg(text, stop="end_turn"):
    return NS(content=[NS(type="text", text=text)], stop_reason=stop, usage=NS(input_tokens=1, output_tokens=1))


def claude_client(**methods):
    return lambda: NS(messages=NS(**methods))


def test_claude_structured_cut_off_json_is_a_cut_off_error():
    def parse(**kwargs):
        Item.model_validate_json('{"a": ')      # what the SDK does with the cut-off text
    with patched(llm, _anthropic_client=claude_client(parse=parse), _record_usage=lambda *a: None):
        raises(llm.LLMCutOffError, lambda: llm._anthropic_structured(CLAUDE, "s", "u", Item, 100))


def test_claude_structured_wrong_format_is_not_called_a_cut_off():
    def parse(**kwargs):
        Item.model_validate_json('{"a": "not a number"}')
    with patched(llm, _anthropic_client=claude_client(parse=parse), _record_usage=lambda *a: None):
        exc = raises(llm.LLMError, lambda: llm._anthropic_structured(CLAUDE, "s", "u", Item, 100))
    assert not isinstance(exc, llm.LLMCutOffError)


def test_claude_text_cut_off():
    with patched(llm, _anthropic_client=claude_client(create=lambda **k: claude_msg("part", "max_tokens")),
                 _record_usage=lambda *a: None):
        raises(llm.LLMCutOffError, lambda: llm._anthropic_text(CLAUDE, "", "u", 100))
    with patched(llm, _anthropic_client=claude_client(create=lambda **k: claude_msg(" ok ")), _record_usage=lambda *a: None):
        assert llm._anthropic_text(CLAUDE, "", "u", 100) == "ok"


def test_claude_chat_answer_cut_off_is_continued():
    sent = []
    queue = [claude_msg("Hello wor", "max_tokens"), claude_msg("ld.")]

    def create(**kwargs):
        sent.append([dict(m) for m in kwargs["messages"]])
        return queue.pop(0)

    with patched(llm, _anthropic_client=claude_client(create=create), _record_usage=lambda *a: None):
        reply = llm._anthropic_tool_loop(CLAUDE, "sys", [{"role": "user", "content": "q"}], [], lambda n, i: "", 4)
    assert reply == "Hello world."
    assert sent[1][-2] == {"role": "assistant", "content": "Hello wor"}
    assert sent[1][-1] == {"role": "user", "content": llm.CONTINUE_PROMPT}


# ---------------------------------------------------------------- Tally

def test_tally_records_only_real_cuts():
    t = Tally()
    assert t.shorten("excerpt", "abc", 10) == "abc" and t.complete
    assert t.shorten("excerpt", "abcdefghij" * 3, 10) == "abcdefghij" and not t.complete
    t.trim("page", 5, 5)
    s = t.summary()
    assert s["trimmed"] == [{"what": "excerpt", "kept": 10, "total": 30, "unit": "characters"}]
    assert s["complete"] is False


def test_tally_keeps_first_reason_largest_cut_and_merges():
    a, b = Tally(), Tally()
    a.miss("CDHDR", "cut off")
    a.miss("CDHDR", "another reason")
    a.trim("x", 10, 20)
    b.trim("x", 10, 100)
    b.miss("CDPOS", "refused")
    b.note("only the first 8 parts were read")
    a.merge(b)
    s = a.summary()
    assert s["unprocessed"] == [{"item": "CDHDR", "reason": "cut off"}, {"item": "CDPOS", "reason": "refused"}]
    assert s["trimmed"][0]["total"] == 100
    assert s["notes"] == ["only the first 8 parts were read"]


def test_tally_sentences_are_short_and_counted():
    t = Tally()
    for i in range(12):
        t.miss(f"T{i}", "cut off")
    lines = t.sentences(limit=5)
    assert len(lines) == 6 and lines[-1] == "... and 7 more."
