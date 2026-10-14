"""Standalone suite (run by test_suites.py): a script that prints PASS/FAIL lines and a final N/N summary."""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import chat, llm
import transactions.db02 as db02

# 1. which messages are refused before any model is called
refuse = [
    "show me rows from T001", "Show me the records of BSEG", "display the entries in LFA1", "list the contents of KNA1",
    "select * from bkpf", "SELECT BELNR FROM BKPF WHERE GJAHR = '2023'", "browse table KNA1", "open SE16N for T001",
    "what is in table T001", "what's in the table MARA?", "show me the field values of VBAK", "dump the line items of BSEG",
    "Can you give me the entries of BKPF for 2023?",
]
allow = [
    "how many entries does BKPF have", "how many rows does BSEG have?", "how big is table BSEG", "show me the largest tables",
    "which archiving object does the DVM Guide recommend for BKPF?", "show me the archiving objects for BKPF",
    "what does the DVM guide say about the data in table BSEG", "list the fields of VBAK", "give me the number of entries for BKPF",
    "has FI_DOCUMNT been archived before?", "show the data volume of BKPF", "what fields does BKPF have",
]
bad = [m for m in refuse if not chat.wants_table_contents(m)] + [m for m in allow if chat.wants_table_contents(m)]
print("guard:", "OK" if not bad else f"WRONG for {bad}")

# 2. a refused request never reaches the model
def boom(*a, **k): raise AssertionError("the model was called")
llm.run_tool_loop = boom; llm.structured = boom; llm.text = boom
out = chat.run_chat("show me rows from T001", [], [], [])
print("refusal reply:", out["reply"] == chat.TABLE_DATA_REFUSAL, out["updated_rows"] is None)

# 3. the tool list: no tool can return table contents
names = [t["name"] for t in chat._TOOLS]
print("tools:", names)
print("no row/stat tools:", not ({"browse_table_contents", "analyze_table"} & set(names)))

# 4. a model that still asks for a removed tool gets the policy text, and no SAP call is made
class Sap:  # any SAP access would fail loudly
    is_connected = True
chat.sap = Sap()
captured = {}
def fake_loop(system, messages, tools, execute_tool, max_rounds, settings):
    captured["system"] = system
    captured["browse"] = execute_tool("browse_table_contents", {"table_name": "KNA1"})
    captured["analyze"] = execute_tool("analyze_table", {"table_name": "KNA1"})
    return "done"
llm.run_tool_loop = fake_loop
llm.load_settings = lambda: llm.Settings(provider="ollama", model="x") if hasattr(llm, "Settings") else None
llm.not_configured_message = lambda s=None: None
llm.budget = lambda s=None: {"chat_context_tables": 25, "chat_history_messages": 6, "chat_max_rounds": 4}
chat.run_chat("how many entries does KNA1 have", [], [], [])
print("removed tools return the policy:", captured["browse"] == chat.TABLE_DATA_REFUSAL and captured["analyze"] == chat.TABLE_DATA_REFUSAL)
print("system prompt has the rule:", "DATA PROTECTION" in captured["system"] and "browse_table_contents" not in captured["system"])

# 5. the count query returns numbers only (SQL Editor faked)
sent = {}
db02.sap.get_session = lambda: object()
db02._open_sql_editor = lambda session: None
def fake_sql(session, query):
    sent["q"] = query
    return [{"TNAME": "BKPF", "RCOUNT": "12,345,678"}, {"TNAME": "T001", "RCOUNT": "7"}]
db02._run_sql_query = fake_sql
res = db02._run_get_table_counts(["bkpf", "t001", "bad name!", "T001"])
print("counts:", res["counts"])
print("query reads only record counts:", "RECORD_COUNT" in sent["q"] and "SELECT *" not in sent["q"] and "bad name" not in sent["q"].lower())
# the tool text
db02.run_get_table_counts = lambda names: {"status": "ok", "counts": {"BKPF": 12345678}}
chat.sap.is_connected = True
print(chat._exec_table_counts(["BKPF", "ZNOPE"]))
