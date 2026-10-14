"""Whatever is shortened before it reaches a model is reported, never silent (Phase 3): scoring excerpts,
housekeeping excerpts, the chat's table tools and its history. Simulated models only."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import chat  # noqa: E402
import dvm_guide  # noqa: E402
import housekeeping  # noqa: E402
import llm  # noqa: E402
import scoring  # noqa: E402
from fakes import TinyModel  # noqa: E402


def candidates(n):
    return [{"Table Name": "BIGTAB", "Table Description": "d", "Volume (GB)": "1", "Volume (MB)": "1",
             "Archiving Object": f"OBJ_{i:02d}", "Object Description": f"object {i}"} for i in range(n)]


def score(rows, excerpt):
    model = TinyModel(max_output=16000, window=1000000)

    def answer(system, user):
        names = [line[2:].split(":")[0] for line in user.splitlines() if line.startswith("- ")]
        cands = [scoring.ScoredCandidate(archiving_object=n, score=50) for n in names]
        if "top_choice" in system or "rationale" in system.lower():
            return scoring.ScoringResponse(candidates=cands, top_choice=names[0], top_rationale="ok")
        return scoring.ScoreList(candidates=cands)

    model.handle(scoring.ScoringResponse, answer)
    model.handle(scoring.ScoreList, answer)
    saved = dvm_guide.get_reference
    dvm_guide.get_reference = lambda table: excerpt
    try:
        with model.install():
            return scoring.score_archiving_objects(rows)
    finally:
        dvm_guide.get_reference = saved


def test_a_shortened_dvm_excerpt_is_reported_with_the_scoring_result():
    result = score(candidates(3), "x" * 50000)
    notes = result["completeness"]["notes"]
    assert len(notes) == 1 and "shortened" in notes[0] and "BIGTAB" in notes[0]
    assert result["completeness"]["complete"] is True          # every table was scored; only its evidence was shorter


def test_an_excerpt_that_fits_is_not_reported():
    result = score(candidates(3), "short excerpt")
    assert result["completeness"] == {"complete": True, "trimmed": [], "unprocessed": [], "notes": []}


def test_merging_a_retry_keeps_the_notes_and_replaces_the_unscored_list():
    previous = {"rows": [{"Table Name": "A"}], "recommended": [{"Table Name": "A"}],
                "completeness": {"complete": False, "trimmed": [], "unprocessed": [{"item": "B", "reason": "limit"}],
                                 "notes": ["The DVM Guide excerpt was shortened ..."]}}
    new = {"rows": [], "recommended": [], "unscored": [], "message": "", "completeness": {"complete": True, "trimmed": [], "unprocessed": [], "notes": []}}
    merged = scoring.merge_results(previous, new)
    assert merged["completeness"]["notes"] == ["The DVM Guide excerpt was shortened ..."]
    assert merged["completeness"]["unprocessed"] == [] and merged["completeness"]["complete"] is True


def test_housekeeping_says_when_only_part_of_the_dvm_section_was_read():
    model = TinyModel(max_output=16000, window=1000000)
    model.handle(housekeeping.HousekeepingResult, lambda s, u: housekeeping.HousekeepingResult(found=False, program="", rationale="No program is named."))
    saved_ref, saved_batch = dvm_guide.get_reference, housekeeping._search_sap_for_me_batch
    dvm_guide.get_reference = lambda table: "y" * 20000
    housekeeping._search_sap_for_me_batch = lambda pending, on_progress, settings: {
        name: {"program": "", "rationale": rationale} for name, rationale in pending.items()
    }
    try:
        with model.install():
            out = housekeeping.find_housekeeping_programs(["BIGTAB"])
    finally:
        dvm_guide.get_reference, housekeeping._search_sap_for_me_batch = saved_ref, saved_batch
    assert "Only the first" in out["BIGTAB"]["rationale"] and "were read" in out["BIGTAB"]["rationale"]


# ---------------------------------------------------------------- chat

def test_the_table_tools_query_every_table_in_groups_and_say_what_they_did_not_query():
    asked = []
    saved = chat.db02.run_get_table_counts, chat.sap
    chat.db02.run_get_table_counts = lambda names: asked.append(list(names)) or {"status": "ok", "counts": {n: 5 for n in names}}
    chat.sap = type("S", (), {"is_connected": True})()
    try:
        out = chat._exec_table_counts([f"T{i:03d}" for i in range(130)])
    finally:
        chat.db02.run_get_table_counts, chat.sap = saved
    assert [len(g) for g in asked] == [30, 30, 30, 30]            # 120 tables in four SAP queries
    assert "10 more table(s) were not queried" in out and out.count("entries") >= 120


def test_a_table_list_within_the_limit_is_fully_answered_without_a_warning():
    saved = chat.db02.run_get_table_sizes, chat.sap
    chat.db02.run_get_table_sizes = lambda names: {"status": "ok", "sizes": {n: {"volume_gb": 1, "volume_mb": 1024} for n in names}}
    chat.sap = type("S", (), {"is_connected": True})()
    try:
        out = chat._exec_table_sizes([f"T{i:03d}" for i in range(45)] + ["T000"])    # (a repeated name counts once)
    finally:
        chat.db02.run_get_table_sizes, chat.sap = saved
    assert "T044" in out and "not queried" not in out


def test_the_model_is_told_when_the_conversation_is_longer_than_it_can_see():
    seen = {}

    def fake_loop(system, messages, tools, execute_tool, max_rounds=8, settings=None, **kwargs):
        seen["system"], seen["messages"] = system, messages
        return "ok"

    model = TinyModel()
    saved = llm.run_tool_loop
    llm.run_tool_loop = fake_loop
    try:
        with model.install():
            llm.budget = llm.budget            # (the real budget: 6 history messages on Groq)
            history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)]
            chat.run_chat("question", [], [], history)
            assert "longer than what you can see" in seen["system"] and len(seen["messages"]) == 7
            chat.run_chat("question", [], [], history[:4])
            assert "longer than what you can see" not in seen["system"]
    finally:
        llm.run_tool_loop = saved
