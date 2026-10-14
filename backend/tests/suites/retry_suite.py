"""Standalone suite (run by test_suites.py): a script that prints PASS/FAIL lines and a final N/N summary."""
import sys, tempfile, time, shutil, re
from pathlib import Path
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import config
tmp = Path(tempfile.mkdtemp(prefix="retry_")); config.RESULTS_DIR = tmp
import main, scoring, llm, dvm_guide, object_descriptions, openpyxl
import transactions.db15 as db15
from fastapi.testclient import TestClient

main.sap.__class__.is_connected = property(lambda self: True)
object_descriptions.remember_from_rows = lambda rows: None
main._ensure_table_sizes = lambda t: t
llm.not_configured_message = lambda s=None: None
llm.load_settings = lambda: llm.Settings(provider="groq", model="qwen/qwen3.8-27b", groq_api_key="x")
llm.max_workers = lambda s=None: 1
dvm_guide.get_reference = lambda t: ""
results = []
def check(name, ok, extra=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

def lookup(tables, on_progress=None):
    rows = [{"Table Name": t, "Table Description": "d", "Volume (GB)": "1", "Volume (MB)": "1", "Archiving Object": f"{t}_{i}", "Object Description": f"o{i}"}
            for t in tables for i in range(4)]
    return {"status": "ok", "rows": rows, "errors": []}
db15.run_batch = lambda tables, on_progress=None: lookup([t["table_name"] for t in tables])

mode = {"limit_tables": {"TWO"}}
def model(system, user, schema, max_tokens=1024, settings=None):
    table = re.search(r"Table: (\S+)", user).group(1)
    if table in mode["limit_tables"]:
        raise llm.LLMRateLimitError("Groq refused the request (tokens per minute (TPM): Limit 8000, Used 7900)")
    names = re.findall(r"^- (\S+):", user, re.M)
    cands = [scoring.ScoredCandidate(archiving_object=n, score=60 + i) for i, n in enumerate(names)]
    if schema is scoring.ScoreList:
        return scoring.ScoreList(candidates=cands)
    return scoring.ScoringResponse(candidates=cands, top_choice=names[-1], top_rationale="best fit")
llm.structured = model

def wait(c, path, t=15):
    end = time.time() + t
    while time.time() < end:
        s = c.get(path).json()
        if s["status"] in ("done", "error"): return s
        time.sleep(0.05)
    return c.get(path).json()

(config.results_folder("TOP_TABLES") / "t.xlsx").write_bytes(main._build_workbook(
    [{"Table Name": n, "Description": "d"} for n in ("ONE", "TWO", "THREE")], ["Table Name", "Description"], "T").getvalue())

with TestClient(main.app) as c:
    c.post("/api/transactions/db15/batch-from-input", json={"filename": "t.xlsx"})
    wait(c, "/api/transactions/db15/batch/progress")
    s = wait(c, "/api/transactions/db15/score/progress")
    r = s["result"]
    check("run finishes (not an error) even though one table was refused", s["status"] == "done" and r["status"] == "ok")
    check("the refused table is listed, the others are scored", [u["table"] for u in r["unscored"]] == ["TWO"] and sum(1 for x in r["rows"] if x["Score"] != "") == 8)
    two = next(x for x in r["recommended"] if x["Table Name"] == "TWO")
    check("the refused table has no recommended object", two["Archiving Object"] == "" and two["Object Description"] == "(not scored)")
    check("the others have a recommended object with the model's rationale", [x["Rationale"] for x in r["recommended"] if x["Table Name"] != "TWO"] == ["best fit", "best fit"])
    saved = openpyxl.load_workbook(r["saved_path"], read_only=True)
    check("the partial result was saved", "Recommended" in saved.sheetnames)

    # retry while the limit still applies: the earlier result is kept
    r1 = c.post("/api/transactions/db15/score", json={"rows": [], "only_unscored": True}); check("retry starts", r1.json()["status"] == "started" and r1.json()["total"] == 1, str(r1.json()))
    s = wait(c, "/api/transactions/db15/score/progress")
    check("a retry that is refused again keeps the earlier result", s["status"] == "done" and len(s["result"]["rows"]) == 12 and [u["table"] for u in s["result"]["unscored"]] == ["TWO"], s["result"].get("message", "")[:70])

    # retry once the limit is gone: only that table is scored, merged in place
    mode["limit_tables"] = set()
    c.post("/api/transactions/db15/score", json={"rows": [], "only_unscored": True})
    s = wait(c, "/api/transactions/db15/score/progress"); r = s["result"]
    check("the retry fills in the missing table and nothing is left unscored", s["status"] == "done" and r["unscored"] == [] and all(x["Score"] != "" for x in r["rows"]))
    check("tables keep their order", [x["Table Name"] for x in r["recommended"]] == ["ONE", "TWO", "THREE"])
    two = next(x for x in r["recommended"] if x["Table Name"] == "TWO")
    check("the table now has its recommendation", two["Archiving Object"] == "TWO_3" and two["Rationale"] == "best fit")
    saved = openpyxl.load_workbook(r["saved_path"], read_only=True, data_only=True)
    names = [row[0] for row in saved["Recommended"].iter_rows(min_row=2, values_only=True)]
    check("the saved workbook was updated too", names.count("TWO") == 1 and saved["Recommended"].max_row == 4)
    check("nothing left to retry is refused", c.post("/api/transactions/db15/score", json={"rows": [], "only_unscored": True}).status_code == 400)

print(f"{sum(results)}/{len(results)} passed")
shutil.rmtree(tmp, ignore_errors=True)
