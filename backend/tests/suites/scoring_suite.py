"""Standalone suite (run by test_suites.py): a script that prints PASS/FAIL lines and a final N/N summary."""
import sys, re, json, types
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import httpx, groq
import llm, scoring, dvm_guide

results = []
def check(name, ok, extra=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

QWEN = llm.Settings(provider="groq", model="qwen/qwen3.8-27b", groq_api_key="x")
CLAUDE = llm.Settings(provider="anthropic", model="claude-haiku-4-5")
llm.not_configured_message = lambda s=None: None
llm.set_wait_hook = lambda h: None

def rows_for(table, n, desc="d"):
    return [{"Table Name": table, "Table Description": desc, "Volume (GB)": "1", "Volume (MB)": "1",
             "Archiving Object": f"OBJ_{i:03d}", "Object Description": f"object {i}"} for i in range(n)]

calls = []
def fake_model(behavior=None):
    """A model that scores candidate OBJ_nnn as nnn % 90 (so OBJ_089 is best) and records every request."""
    def structured(system, user, schema, max_tokens=1024, settings=None):
        names = re.findall(r"^- (\S+):", user, re.M)
        calls.append({"schema": schema.__name__, "n": len(names), "max_tokens": max_tokens, "names": names})
        if behavior:
            behavior(names, schema)
        cands = [scoring.ScoredCandidate(archiving_object=n, score=int(n.split("_")[1]) % 90) for n in names]
        if schema is scoring.ScoreList:
            return scoring.ScoreList(candidates=cands)
        best = max(cands, key=lambda c: c.score)
        return scoring.ScoringResponse(candidates=cands, top_choice=best.archiving_object, top_rationale=f"{best.archiving_object} fits")
    return structured

def run(settings, rows, model):
    calls.clear()
    llm.load_settings = lambda: settings
    llm.structured = model
    llm.budget = lambda s=None: {"dvm_excerpt_chars": 3000, "score_chunk": 40 if s is None or settings.provider != "anthropic" else 120}
    llm.max_workers = lambda s=None: 1
    dvm_guide.get_reference = lambda t: "The guide recommends OBJ_003 and OBJ_007 for this table." if t == "BIG" else ""
    msgs = []
    out = scoring.score_archiving_objects(rows, on_progress=lambda c, m: msgs.append((c, m)))
    return out, msgs

# ── a table of 374 candidates on Qwen: chunks of 40 then a playoff ─────────────────────────────────
out, msgs = run(QWEN, rows_for("BIG", 374), fake_model())
chunk_calls = [c for c in calls if c["schema"] == "ScoreList"]; final_calls = [c for c in calls if c["schema"] == "ScoringResponse"]
check("374 candidates -> 10 parts of at most 40", len(chunk_calls) == 10 and max(c["n"] for c in chunk_calls) <= 40, f"{len(chunk_calls)} parts")
check("then one playoff of at most 8 candidates", len(final_calls) == 1 and final_calls[0]["n"] <= 8, str(final_calls[0]["n"]))
check("the guide's named objects are in the playoff", {"OBJ_003", "OBJ_007"} <= set(final_calls[0]["names"]))
check("every request asks for at most Qwen's 1,000 output tokens", all(c["max_tokens"] <= 1000 for c in calls), str(max(c["max_tokens"] for c in calls)))
rec = out["recommended"][0]
check("the winner is recommended with the model's rationale", rec["Archiving Object"] == "OBJ_089" and rec["Rationale"] == "OBJ_089 fits", f"{rec['Archiving Object']} / {rec['Rationale']}")
scored = {r["Archiving Object"]: int(r["Score"]) for r in out["rows"] if r["Score"] != ""}
fin = set(final_calls[0]["names"]); lowest_final = min(scored[n] for n in fin)
check("all 374 candidates have a score", len(scored) == 374)
check("nothing outside the playoff scores above a finalist", all(s < lowest_final for n, s in scored.items() if n not in fin) or lowest_final == 0)
check("the other candidates carry no rationale", sum(1 for r in out["recommended"] if r.get("Rationale")) == 1)
check("steps are reported without moving the table counter", any("part 3 of 10" in m for _, m in msgs) and any("comparing" in m for _, m in msgs))
check("no unscored tables", out["unscored"] == [] and out["status"] == "ok")

# ── Claude: bigger parts ───────────────────────────────────────────────────────────────────────────
out, _ = run(CLAUDE, rows_for("BIG", 374), fake_model())
check("on Claude 374 candidates -> 4 parts of at most 120", len([c for c in calls if c["schema"] == "ScoreList"]) == 4)

# ── small table: one call, rationale on the best ──────────────────────────────────────────────────
out, _ = run(QWEN, rows_for("SMALL", 12), fake_model())
check("12 candidates -> a single call", len(calls) == 1 and calls[0]["schema"] == "ScoringResponse")

# ── an answer that comes back cut off is split and asked again ────────────────────────────────────
def cut_off(names, schema):
    if len(names) > 20: raise llm.LLMError("The model's answer was cut off before it finished (output limit reached).")
out, _ = run(QWEN, rows_for("MID", 40), fake_model(cut_off))
n_scored = sum(1 for r in out["rows"] if r["Score"] != "")
check("a cut-off answer is split until it fits: all 40 scored", n_scored == 40 and len(calls) > 1, f"{len(calls)} calls")

# ── candidates the model leaves out are asked for once more ───────────────────────────────────────
state = {"first": True}
def skip_three(names, schema):
    pass
base = fake_model()
def forgetful(system, user, schema, max_tokens=1024, settings=None):
    res = base(system, user, schema, max_tokens, settings)
    if state["first"] and schema is scoring.ScoringResponse:
        state["first"] = False
        res.candidates = res.candidates[:-3]
    return res
out, _ = run(QWEN, rows_for("MISS", 10), forgetful)
check("left-out candidates are asked for again", len(calls) == 2 and calls[1]["n"] == 3 and all(r["Score"] != "" for r in out["rows"]), str([c["n"] for c in calls]))

# ── a limit stops the run: finished tables are kept, the rest listed, nothing guessed ────────────
def stop_at_table_c(names, schema):
    if "TBL_C" in stop_at_table_c.current: raise llm.LLMRateLimitError("Groq refused: tokens per minute (TPM): Limit 8000, Used 7900")
order = []
base2 = fake_model()
def limited(system, user, schema, max_tokens=1024, settings=None):
    table = re.search(r"Table: (\S+)", user).group(1); order.append(table)
    if table in ("TBL_C", "TBL_D"): raise llm.LLMRateLimitError("Groq refused the request (tokens per minute (TPM): Limit 8000)")
    return base2(system, user, schema, max_tokens, settings)
rows = rows_for("TBL_A", 5) + rows_for("TBL_B", 5) + rows_for("TBL_C", 5) + rows_for("TBL_D", 5)
out, _ = run(QWEN, rows, limited)
check("a limit keeps the tables that finished", out["status"] == "ok" and sum(1 for r in out["rows"] if r["Score"] != "") == 10)
check("the others are listed with the reason", [u["table"] for u in out["unscored"]] == ["TBL_C", "TBL_D"] and "Groq refused" in out["unscored"][0]["reason"])
unscored_rec = [r for r in out["recommended"] if r["Table Name"] in ("TBL_C", "TBL_D")]
check("an unscored table has no recommended object (not the first candidate)", all(r["Archiving Object"] == "" and r["Object Description"] == "(not scored)" and "Groq refused" in r["Rationale"] for r in unscored_rec))
check("the message says how many", "2 of 4" in out["message"], out["message"][:60])

# nothing at all could be scored because of a limit -> an error like before
def always_limited(system, user, schema, max_tokens=1024, settings=None): raise llm.LLMRateLimitError("daily limit reached", daily=True)
out, _ = run(QWEN, rows_for("X", 5) + rows_for("Y", 5), always_limited)
check("a limit before the first table is an error", out["status"] == "error" and "daily limit" in out["message"])

# a bad key ends the run
def bad_key(system, user, schema, max_tokens=1024, settings=None): raise llm.LLMAuthError("bad key")
out, _ = run(QWEN, rows_for("X", 5), bad_key)
check("a rejected key ends the run", out["status"] == "error" and "bad key" in out["message"])

# a model that returns a tie on top_choice: generic rationale on the app's best
def odd_top(system, user, schema, max_tokens=1024, settings=None):
    names = re.findall(r"^- (\S+):", user, re.M)
    cands = [scoring.ScoredCandidate(archiving_object=n, score=50) for n in names]
    return scoring.ScoringResponse(candidates=cands, top_choice=names[-1], top_rationale="the last one is best")
out, _ = run(QWEN, rows_for("TIE", 4), odd_top)
check("top pick not the app's best -> generic rationale on the app's best", out["recommended"][0]["Archiving Object"] == "OBJ_003" if False else out["recommended"][0]["Rationale"].startswith("Highest score (50)"), out["recommended"][0]["Rationale"])

# ── retry merge ─────────────────────────────────────────────────────────────────────────────────────
prev = {"status": "ok", "rows": [{"Table Name": "A", "Score": "90"}, {"Table Name": "B", "Score": ""}, {"Table Name": "B", "Score": ""}, {"Table Name": "C", "Score": "80"}],
        "recommended": [{"Table Name": "A", "Archiving Object": "a"}, {"Table Name": "B", "Archiving Object": ""}, {"Table Name": "C", "Archiving Object": "c"}],
        "unscored": [{"table": "B", "reason": "limit"}], "message": "1 of 3"}
new = {"status": "ok", "rows": [{"Table Name": "B", "Score": "70"}, {"Table Name": "B", "Score": "60"}], "recommended": [{"Table Name": "B", "Archiving Object": "b1"}], "unscored": [], "message": ""}
m = scoring.merge_results(prev, new)
check("merge keeps the table order and replaces only the re-run table", [r["Table Name"] + r["Score"] for r in m["rows"]] == ["A90", "B70", "B60", "C80"] and [r["Archiving Object"] for r in m["recommended"]] == ["a", "b1", "c"])
check("merge takes unscored and message from the re-run", m["unscored"] == [] and m["message"] == "")

# ═══ the Groq limiter ═══════════════════════════════════════════════════════════════════════════════
OTPM_TEXT = ("Request too large for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on output tokens per minute (OTPM): "
             "Limit 1000, Requested 1551. The request's expected output tokens exceed the enforced limit; reduce max_tokens. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com")
def rate_error(text):
    return groq.RateLimitError(text, response=httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com")), body={"error": {"message": text}})

class Resp:
    def __init__(self, out=100):
        self.usage = types.SimpleNamespace(total_tokens=out + 500, completion_tokens=out, prompt_tokens=500)
        self.choices = [types.SimpleNamespace(finish_reason="stop", message=types.SimpleNamespace(content="{}"))]

def fake_client(script):
    seen = []
    def create(**kw):
        seen.append(dict(kw)); step = script[min(len(seen) - 1, len(script) - 1)]
        if isinstance(step, Exception): raise step
        return step if isinstance(step, Resp) else Resp()
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))), seen

class Clock:
    def __init__(self): self.t = 1000.0; self.slept = []
    def monotonic(self): return self.t
    def sleep(self, s): self.slept.append(s); self.t += s
clock = Clock(); llm.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
msgs_ = [("user", "hi")]
messages = [{"role": "user", "content": "x" * 3000}]

# 1. an unknown model teaches itself the output limit from the real message, and asks for less at once
llm._learned_otpm.clear(); llm._windows.clear()
other = llm.Settings(provider="groq", model="some/new-model", groq_api_key="x")
client, seen = fake_client([rate_error(OTPM_TEXT.replace("qwen/qwen3.8-27b", "some/new-model")), None])
llm._groq_client = lambda key: client
llm._groq_chat(other, messages, max_tokens=4000)
check("OTPM refusal: learned, resent with at most that many output tokens", llm._learned_otpm.get("some/new-model") == 1000 and seen[0]["max_completion_tokens"] > 1000 and seen[1]["max_completion_tokens"] <= 1000, str([s["max_completion_tokens"] for s in seen]))
check("... without waiting", clock.slept == [])

# 2. a known model never asks for more than its limit
llm._windows.clear(); clock.slept.clear()
client, seen = fake_client([None]); llm._groq_client = lambda key: client
llm._groq_chat(QWEN, messages, max_tokens=4000)
check("Qwen: max_completion_tokens capped at 1,000", seen[0]["max_completion_tokens"] <= 1000, str(seen[0]["max_completion_tokens"]))
check("Qwen: reasoning is switched off", seen[0].get("reasoning_effort") == "none")

# 3. the output allowance is paced by what was really produced (live check: Groq does not charge unused output)
llm._windows.clear(); clock.slept.clear()
client, seen = fake_client([Resp(out=100)]); llm._groq_client = lambda key: client
llm._groq_chat(QWEN, messages, max_tokens=700); first_time = clock.t
llm._groq_chat(QWEN, messages, max_tokens=700)
check("asking for 700 twice but producing 100 each does not wait", sum(clock.slept) == 0, f"waited {clock.t - first_time:.0f}s")
llm._windows.clear(); clock.slept.clear()
client, seen = fake_client([Resp(out=700)]); llm._groq_client = lambda key: client
llm._groq_chat(QWEN, messages, max_tokens=700); first_time = clock.t
llm._groq_chat(QWEN, messages, max_tokens=700)
check("a second request that would exceed the produced output waits for the minute to pass", clock.t - first_time >= 59 and sum(clock.slept) >= 59, f"waited {clock.t - first_time:.0f}s")

# 4. a request that is "too large" for TPM is not resent
llm._windows.clear(); clock.slept.clear()
tpm_text = "Request too large for model `x` in organization `o` on tokens per minute (TPM): Limit 8000, Requested 9500. Reduce your message size."
client, seen = fake_client([rate_error(tpm_text)]); llm._groq_client = lambda key: client
try:
    llm._groq_chat(QWEN, messages, max_tokens=500); check("TPM too large is not resent", False)
except llm.LLMTooLargeError as e:
    check("TPM too large is raised at once, not resent 6 times", len(seen) == 1 and "9500" in str(e), str(e)[:80])

# 5. an ordinary per-minute refusal waits and retries, and the final message carries Groq's own text
llm._windows.clear(); clock.slept.clear(); llm._learned_otpm.clear()
busy = "Rate limit reached for model `x` in organization `o` on tokens per minute (TPM): Limit 8000, Used 7900, Requested 500. Please try again in 12s."
client, seen = fake_client([rate_error(busy)] * 10); llm._groq_client = lambda key: client
try:
    llm._groq_chat(QWEN, messages, max_tokens=500)
except llm.LLMRateLimitError as e:
    check("a busy minute is retried, then reported with Groq's own words", len(seen) == llm.MAX_RATE_RETRIES and "Used 7900" in str(e) and "organization" not in str(e), str(e)[:140])

# 6. the daily limit still stops at once
llm._windows.clear(); llm._daily_block.clear()
daily = "Rate limit reached for model `x` on tokens per day (TPD): Limit 200000, Used 199900, Requested 500."
client, seen = fake_client([rate_error(daily)]); llm._groq_client = lambda key: client
try:
    llm._groq_chat(QWEN, messages, max_tokens=500)
except llm.LLMRateLimitError as e:
    check("daily limit stops at once", len(seen) == 1 and e.daily)
llm._daily_block.clear()

# 7. a model that does not take the reasoning parameter is retried without it
llm._windows.clear()
bad = groq.BadRequestError("reasoning_effort is not supported for this model", response=httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com")), body={"error": {"message": "reasoning_effort is not supported"}})
client, seen = fake_client([bad, None]); llm._groq_client = lambda key: client
llm._reasoning_unsupported.clear()
llm._groq_chat(QWEN, messages, max_tokens=500)
check("reasoning parameter dropped after a refusal", "reasoning_effort" in seen[0] and "reasoning_effort" not in seen[1] and QWEN.model in llm._reasoning_unsupported)

print(f"{sum(results)}/{len(results)} passed")
