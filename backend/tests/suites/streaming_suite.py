"""Standalone suite (run by test_suites.py): a script that prints PASS/FAIL lines and a final N/N summary."""
import sys, json, threading, time, types, http.server, re
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import llm, chat, main
from fastapi.testclient import TestClient

results = []
def check(name, ok, extra=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

NS = types.SimpleNamespace
def norm(t): return chr(10).join(l.rstrip() for l in t.split(chr(10))).strip()
NN = chr(10) * 2

# ---------------------------------------------------------------- Groq: chunks -> response
def gchunk(content=None, tool_calls=None, finish=None, usage=None, x_usage=None):
    delta = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=usage, x_groq=NS(usage=x_usage) if x_usage else None)

def tcall(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))

got = []
r = llm._groq_collect(iter([gchunk("Hel"), gchunk("lo "), gchunk("world"), gchunk(finish="stop"), gchunk(x_usage=NS(total_tokens=42, completion_tokens=3))]), got.append)
check("Groq: text pieces are passed on in order and joined", got == ["Hel", "lo ", "world"] and r.choices[0].message.content == "Hello world", str(got))
check("Groq: usage taken from the final chunk", r.usage.total_tokens == 42)
check("Groq: no tool calls -> None", r.choices[0].message.tool_calls is None)

r = llm._groq_collect(iter([
    gchunk(tool_calls=[tcall(0, "call_1", "search_dvm_guide", '{"que')]), gchunk(tool_calls=[tcall(0, None, None, 'ry": "BKPF"}')]),
    gchunk(tool_calls=[tcall(1, "call_2", "get_table_counts", "{}")]), gchunk(finish="tool_calls"),
]), got.append)
calls = r.choices[0].message.tool_calls
check("Groq: a tool call split over chunks is put back together", len(calls) == 2 and calls[0].function.name == "search_dvm_guide" and json.loads(calls[0].function.arguments) == {"query": "BKPF"} and calls[1].id == "call_2")

import groq
class Boom:
    def __iter__(self):
        yield gchunk("part")
        raise groq.APIConnectionError(request=NS())
try:
    llm._groq_collect(Boom(), lambda p: None); check("Groq: a failure part-way is reported", False)
except llm.LLMError as e:
    check("Groq: a failure part-way is reported as an LLMError", "part-way" in str(e), str(e)[:60])

# ---------------------------------------------------------------- think filter
def run_filter(pieces):
    out = []; feed, flush = llm._hide_think(out.append)
    for p in pieces: feed(p)
    flush(); return "".join(out)
check("think filter: plain text untouched", run_filter(["Hello ", "there"]) == "Hello there")
check("think filter: a think block is removed", run_filter(["<think>secret</think>Answer"]) == "Answer")
check("think filter: tags split across pieces", run_filter(["Hi <th", "ink>hidden ", "</thi", "nk>there"]) == "Hi there")
check("think filter: a lone '<' is kept", run_filter(["a < b", " and c"]) == "a < b and c")
check("think filter: text ending in a partial tag is flushed", run_filter(["x <thi"]) == "x <thi")

# ---------------------------------------------------------------- Ollama: real HTTP, NDJSON
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n))
        if body.get("model") == "missing":
            self.send_response(404); self.end_headers(); self.wfile.write(json.dumps({"error": "model 'missing' not found"}).encode()); return
        self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
        lines = [{"message": {"role": "assistant", "content": "One "}, "done": False},
                 {"message": {"role": "assistant", "content": "two"}, "done": False},
                 {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_table_counts", "arguments": {"table_names": ["BSEG"]}}}]}, "done": False},
                 {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5}]
        for l in lines: self.wfile.write((json.dumps(l) + chr(10)).encode()); self.wfile.flush(); time.sleep(0.05)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
llm._ollama_base = lambda: f"http://127.0.0.1:{srv.server_address[1]}"
pieces = []
data = llm._ollama_request("POST", "/api/chat", {"model": "m", "stream": True}, timeout=10, on_piece=pieces.append)
check("Ollama: pieces streamed and merged", pieces == ["One ", "two"] and data["message"]["content"] == "One two" and data["eval_count"] == 5, str(pieces))
check("Ollama: tool call kept", data["message"]["tool_calls"][0]["function"]["name"] == "get_table_counts")
try:
    llm._ollama_request("POST", "/api/chat", {"model": "missing"}, timeout=10, on_piece=pieces.append); check("Ollama: error status raised", False)
except llm.LLMError as e:
    check("Ollama: an error status on a streamed request gives the usual message", "ollama pull missing" in str(e), str(e)[:70])

# ---------------------------------------------------------------- Anthropic: fake stream object
class FakeStream:
    def __init__(self, texts, final): self.text_stream = iter(texts); self._final = final
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self._final
tool_block = NS(type="tool_use", id="t1", name="get_table_counts", input={"table_names": ["BSEG"]})
rounds = [FakeStream(["Let me check. "], NS(stop_reason="tool_use", content=[NS(type="text", text="Let me check. "), tool_block], usage=NS(input_tokens=5, output_tokens=5))),
          FakeStream(["BSEG has ", "5 entries."], NS(stop_reason="end_turn", content=[NS(type="text", text="BSEG has 5 entries.")], usage=NS(input_tokens=5, output_tokens=5)))]
fake_client = NS(messages=NS(stream=lambda **kw: rounds.pop(0)))
llm._anthropic_client = lambda: fake_client
llm._record_usage = lambda *a: None
streamed = []
reply = llm._anthropic_tool_loop(llm.Settings(provider="anthropic", model="claude-x"), "sys", [{"role": "user", "content": "hi"}], [], lambda n, i: "5", 4, streamed.append)
check("Claude: the loop streams both rounds and returns what was streamed", reply == norm("".join(streamed)) and reply == "Let me check." + NN + "BSEG has 5 entries.", repr(reply))
check("Claude: a blank line separates text written before a tool call", streamed == ["Let me check. ", NN, "BSEG has ", "5 entries."], str(streamed))

# ---------------------------------------------------------------- Groq tool loop end to end with a fake client
class FakeGroqClient:
    def __init__(self, scripts): self.scripts = scripts; self.kw = []
    @property
    def chat(self): return NS(completions=NS(create=self._create))
    def _create(self, **kw):
        self.kw.append(kw); return iter(self.scripts.pop(0))
script = [
    [gchunk("Checking. "), gchunk(tool_calls=[tcall(0, "c1", "search_dvm_guide", '{"query": "BKPF"}')]), gchunk(finish="tool_calls", x_usage=NS(total_tokens=300, completion_tokens=20))],
    [gchunk("The DVM "), gchunk("Guide says FI_DOCUMNT."), gchunk(finish="stop", x_usage=NS(total_tokens=500, completion_tokens=30))],
]
fc = FakeGroqClient(script); llm._groq_client = lambda key: fc; llm._windows.clear()
s = llm.Settings(provider="groq", model="openai/gpt-oss-20b", groq_api_key="x")
exec_log = []
streamed = []
reply = llm._groq_tool_loop(s, "sys", [{"role": "user", "content": "q"}], [{"name": "search_dvm_guide", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
                            lambda n, i: exec_log.append((n, i)) or "result", 4, streamed.append)
check("Groq loop: streamed request flags set", fc.kw[0].get("stream") is True and "stream_options" not in fc.kw[0])
check("Groq loop: tool executed with the assembled arguments", exec_log == [("search_dvm_guide", {"query": "BKPF"})])
check("Groq loop: reply equals the streamed text", reply == "Checking." + NN + "The DVM Guide says FI_DOCUMNT." and norm("".join(streamed)) == reply, repr(reply))
check("Groq loop: usage of the streamed answer was counted", llm._windows[s.model] is not None)

# the non-streaming path is untouched
fc2 = FakeGroqClient([])
class Resp:
    def __init__(self): self.usage = NS(total_tokens=100, completion_tokens=10); self.choices = [NS(finish_reason="stop", message=NS(content=" plain answer ", tool_calls=None))]
fc2._create = lambda **kw: (fc2.kw.append(kw), Resp())[1]
llm._groq_client = lambda key: fc2
check("non-streaming Groq loop unchanged", llm._groq_tool_loop(s, "sys", [{"role": "user", "content": "q"}], [], lambda n, i: "", 4) == "plain answer" and "stream" not in fc2.kw[0])

# ---------------------------------------------------------------- the endpoint
llm.not_configured_message = lambda s=None: None
llm.load_settings = lambda: s
fc3 = FakeGroqClient([[gchunk("Hello "), gchunk("there."), gchunk(finish="stop", x_usage=NS(total_tokens=100, completion_tokens=5))]])
llm._groq_client = lambda key: fc3
client = TestClient(main.app)
def stream(msg, **extra):
    body = {"message": msg, "scored_rows": [], "recommended": [], "history": [], **extra}
    with client.stream("POST", "/api/chat/stream", json=body) as r:
        return r.status_code, r.headers.get("content-type"), [json.loads(l) for l in r.iter_lines() if l]
code, ctype, ev = stream("What is an archiving object?")
check("endpoint: 200 and NDJSON", code == 200 and "ndjson" in ctype, ctype)
check("endpoint: token events then one done event", [e["type"] for e in ev] == ["token", "token", "done"], str([e["type"] for e in ev]))
check("endpoint: done carries the full reply and no row changes", ev[-1]["reply"] == "Hello there." and ev[-1]["updated_rows"] is None)

used_before = len(fc3.kw)
code, ctype, ev = stream("show me the rows of table BSEG")
check("data policy: a table-content request is refused with no model call and no tokens", [e["type"] for e in ev] == ["done"] and ev[0]["reply"] == chat.TABLE_DATA_REFUSAL and len(fc3.kw) == used_before)

# errors after some text keep the text
def failing(system, messages, tools, execute_tool, max_rounds=8, settings=None, on_token=None):
    on_token("Partial "); on_token("answer."); raise llm.LLMRateLimitError("Groq limit reached", daily=False)
orig_loop = llm.run_tool_loop; llm.run_tool_loop = failing
code, ctype, ev = stream("anything")
check("error part-way: the text already shown is kept, the reason follows", ev[-1]["type"] == "done" and ev[-1]["reply"] == "Partial answer." + NN + "Groq limit reached", repr(ev[-1].get("reply")))

# status events and tool names
def with_tool(system, messages, tools, execute_tool, max_rounds=8, settings=None, on_token=None):
    on_token("Looking. "); out = execute_tool("get_table_counts", {"table_names": []}); on_token("Done."); return "Looking. Done."
llm.run_tool_loop = with_tool
chat._exec_table_counts = lambda names: "none"
code, ctype, ev = stream("how many rows in BSEG")
types_ = [e["type"] for e in ev]
check("status event is sent before the tool runs", types_ == ["token", "status", "token", "done"] and ev[1]["text"] == chat.TOOL_STATUS["get_table_counts"], str(types_))
check("status texts mention no transaction codes", not any(re.search(r"\b(DB0?2|DB15|SE1[16]|SARA|AOBJ|TAANA)\b", t) for t in chat.TOOL_STATUS.values()))

# unexpected exception -> error event
def crash(*a, **k): raise RuntimeError("boom")
llm.run_tool_loop = crash
code, ctype, ev = stream("anything")
check("unexpected exception becomes an error event", ev[-1]["type"] == "error" and "boom" in ev[-1]["message"])

# the old endpoint still works
llm.run_tool_loop = lambda *a, **k: "plain reply"
r = client.post("/api/chat", json={"message": "hi", "scored_rows": [], "recommended": [], "history": []})
check("/api/chat unchanged", r.status_code == 200 and r.json()["reply"] == "plain reply")

print(f"{sum(results)}/{len(results)} passed")
