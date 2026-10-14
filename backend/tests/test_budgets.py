"""Budgets follow the model's real limits: a calibrated token estimate, a chat answer that is not squeezed by an
over-cautious estimate, and caps that shrink for a model with a small window (Phase 4). Simulated models only."""

import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat  # noqa: E402
import llm  # noqa: E402

NS = types.SimpleNamespace


def fresh():
    llm._calibrated_cpt.clear()
    llm._windows.clear()


def test_the_ratio_is_measured_for_known_models_and_safe_for_unknown_ones():
    fresh()
    assert llm.chars_per_token("openai/gpt-oss-20b") == 4.2
    assert llm.chars_per_token("openai/gpt-oss-120b") == 4.2
    assert llm.chars_per_token("qwen/qwen3.8-27b") == 3.5
    assert llm.chars_per_token("some/new-model") == llm.DEFAULT_CHARS_PER_TOKEN == 3.2
    assert llm.chars_per_token("") == 3.2


def test_the_ratio_is_learned_from_real_replies_and_stays_on_the_safe_side():
    fresh()
    llm._learn_chars_per_token("m", 6000, 1500)             # observed 4.0 characters per token
    assert abs(llm.chars_per_token("m") - 3.6) < 1e-9         # stored 10% lower: estimates stay above the truth
    llm._learn_chars_per_token("m", 6000, 1500)
    assert abs(llm.chars_per_token("m") - 3.6) < 1e-9
    llm._learn_chars_per_token("m", 5000, 2500)              # a denser text: 2.0 per token
    assert 2.5 < llm.chars_per_token("m") < 3.6
    before = llm.chars_per_token("m")
    llm._learn_chars_per_token("m", 100, 20)                 # too small to say anything
    llm._learn_chars_per_token("", 6000, 1500)
    assert llm.chars_per_token("m") == before and "" not in llm._calibrated_cpt


def test_the_estimate_uses_the_model_ratio():
    fresh()
    messages = [{"role": "user", "content": "x" * 3000}]
    chars = len(json.dumps(messages))
    assert llm._estimate_tokens(messages) == int(chars / 3.2) + 20
    assert llm._estimate_tokens(messages, None, "openai/gpt-oss-20b") == int(chars / 4.2) + 20
    assert llm._estimate_tokens(messages, None, "openai/gpt-oss-20b") < llm._estimate_tokens(messages, None, "other")


class FakeGroq:
    def __init__(self):
        self.kw = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.kw.append(kw)
        return NS(usage=NS(total_tokens=100, completion_tokens=10, prompt_tokens=90),
                  choices=[NS(finish_reason="stop", message=NS(content="ok", tool_calls=None))])


def chat_request(model, tool_result_chars):
    rows = [{"Table Name": f"TAB{i:03d}", "Archiving Object": f"OBJ_{i}", "Score": "80", "Rationale": "x" * 80} for i in range(60)]
    s = llm.Settings(provider="groq", model=model, groq_api_key="x")
    ctx, n = chat._build_context(rows, llm.budget(s)["chat_context_tables"])
    system = chat._SYSTEM.format(results_section=chat._RESULTS_LOADED.format(context=ctx, table_count=n))
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in chat._TOOLS]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "q"}]
    if tool_result_chars:
        messages.append({"role": "tool", "content": "x" * tool_result_chars})
    client = FakeGroq()
    old = llm._groq_client, llm._record_usage
    llm._groq_client, llm._record_usage = (lambda key: client), (lambda *a: None)
    try:
        fresh()
        llm._groq_chat(s, messages, max_tokens=1500, tools=tools)
    finally:
        llm._groq_client, llm._record_usage = old
    return client.kw[0]["max_completion_tokens"]


def test_a_chat_answer_after_three_sap_for_me_articles_is_no_longer_squeezed_to_200_tokens():
    # (the old estimate of this request was ~7,500 tokens, which left 200 tokens for the answer; Groq counted far fewer)
    assert chat_request("openai/gpt-oss-20b", 10500) >= 1000


def test_a_normal_chat_answer_still_gets_its_full_allowance_and_qwen_keeps_its_output_limit():
    assert chat_request("openai/gpt-oss-20b", 0) == 1500
    assert chat_request("qwen/qwen3.8-27b", 0) == 1000          # Qwen: 1,000 output tokens per minute


def test_a_request_that_is_really_too_large_is_still_refused():
    try:
        chat_request("openai/gpt-oss-20b", 60000)
    except llm.LLMTooLargeError:
        return
    raise AssertionError("a request far above the per-minute limit must be refused")


def test_budgets_are_unchanged_for_groq_and_claude_and_shrink_for_a_small_local_model():
    groq = llm.Settings(provider="groq", model="m", groq_api_key="x")
    claude = llm.Settings(provider="anthropic", model="claude-x")
    ollama = llm.Settings(provider="ollama", model="small")
    assert llm.budget(groq)["reference_chars"] == 8000 and llm.budget(groq)["score_chunk"] == 40
    assert llm.budget(claude)["reference_chars"] == 14000 and llm.budget(claude)["score_chunk"] == 120
    old = llm._ollama_model_info
    try:
        llm._ollama_model_info = lambda m: {"context": 32768}
        assert llm.input_window_tokens(ollama) == llm.OLLAMA_NUM_CTX
        assert llm.budget(ollama)["reference_chars"] >= 7900                # the window it is run with is about what the values were sized for
        llm._ollama_model_info = lambda m: {"context": 2048}
        small = llm.budget(ollama)
        assert llm.input_window_tokens(ollama) == 2048
        assert small["reference_chars"] == 2048 and small["dvm_excerpt_chars"] == 768 and small["score_chunk"] == 10
        assert small["chat_history_messages"] == 2 and small["chat_context_tables"] == 6
        llm._ollama_model_info = lambda m: {"context": 128}                 # (absurdly small: the floors apply)
        tiny = llm.budget(ollama)
        assert tiny["reference_chars"] == 600 and tiny["score_chunk"] == 5 and tiny["chat_history_messages"] == 2
    finally:
        llm._ollama_model_info = old
