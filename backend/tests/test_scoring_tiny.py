"""Scoring gives the same result on a model with a tiny output limit as on a big one (the original failure: answers cut
off on a model that cannot give 374 scores at once). Simulated models only."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dvm_guide  # noqa: E402
import llm  # noqa: E402
import scoring  # noqa: E402
from fakes import TinyModel  # noqa: E402


def points(name: str) -> int:
    """A deterministic score for an object name, so the right answer is known whatever the model."""
    return sum(ord(c) for c in name) % 90 + 5


def rows(table: str, n: int) -> list[dict]:
    return [{"Table Name": table, "Table Description": "d", "Volume (GB)": "1", "Volume (MB)": "1",
             "Archiving Object": f"OBJ_{i:03d}", "Object Description": f"object {i}"} for i in range(n)]


def names_in(user: str) -> list[str]:
    return [line[2:].split(":")[0] for line in user.splitlines() if line.startswith("- ")]


def answer(system, user):
    names = names_in(user)
    cands = [scoring.ScoredCandidate(archiving_object=n, score=points(n)) for n in names]
    best = max(cands, key=lambda c: c.score)
    return scoring.ScoringResponse(candidates=cands, top_choice=best.archiving_object, top_rationale="best fit")


def parts_answer(system, user):
    return scoring.ScoreList(candidates=[scoring.ScoredCandidate(archiving_object=n, score=points(n)) for n in names_in(user)])


def score(model, all_rows):
    model.handle(scoring.ScoringResponse, answer)
    model.handle(scoring.ScoreList, parts_answer)
    saved = dvm_guide.get_reference
    dvm_guide.get_reference = lambda table: ""
    try:
        with model.install():
            return scoring.score_archiving_objects(all_rows)
    finally:
        dvm_guide.get_reference = saved


def test_a_table_with_hundreds_of_candidates_is_scored_completely_on_a_tiny_model():
    data = rows("CDHDR", 374)                    # the real case: 374 candidate objects for one table
    tiny = score(TinyModel(max_output=300, window=8000), data)
    big = score(TinyModel(max_output=16000, window=200000), data)
    assert tiny["status"] == "ok" and tiny["unscored"] == [] and tiny["completeness"]["complete"] is True
    assert sum(1 for r in tiny["rows"] if r["Score"] != "") == 374
    best = max(data, key=lambda r: points(r["Archiving Object"]))["Archiving Object"]
    assert tiny["recommended"][0]["Archiving Object"] == best == big["recommended"][0]["Archiving Object"]
    assert tiny["recommended"][0]["Rationale"] == "best fit"


def test_many_tables_give_the_same_recommendations_and_top_scores_on_both_models():
    data = rows("TAB_A", 12) + rows("TAB_B", 45) + rows("TAB_C", 3)
    tiny_model, big_model = TinyModel(max_output=300, window=8000), TinyModel(max_output=16000, window=200000)
    tiny, big = score(tiny_model, data), score(big_model, data)
    assert tiny["unscored"] == [] and all(r["Score"] != "" for r in tiny["rows"])
    assert [r["Archiving Object"] for r in tiny["recommended"]] == [r["Archiving Object"] for r in big["recommended"]]
    key = lambda r: (r["Table Name"], r["Archiving Object"])
    t_scores = {key(r): int(r["Score"]) for r in tiny["rows"]}
    b_scores = {key(r): int(r["Score"]) for r in big["rows"]}
    for table in ("TAB_A", "TAB_B", "TAB_C"):
        top = sorted((k for k in b_scores if k[0] == table), key=lambda k: -b_scores[k])[:8]
        assert all(t_scores[k] == b_scores[k] for k in top), table      # the best candidates are scored identically
    # parts are scored apart, so everything outside the playoff is held below the playoff's lowest score: never above the big model's
    assert all(t_scores[k] <= b_scores[k] for k in b_scores)
    assert len(tiny_model.calls) > len(big_model.calls)


def test_a_model_that_cannot_carry_even_one_part_reports_the_table_instead_of_guessing():
    result = score(TinyModel(max_output=30, window=8000), rows("CDHDR", 60))    # not even a few scores fit
    assert result["status"] == "ok" and [u["table"] for u in result["unscored"]] == ["CDHDR"]
    assert sum(1 for r in result["rows"] if r["Score"] != "") == 0
    assert result["recommended"][0]["Archiving Object"] == "" and result["recommended"][0]["Object Description"] == "(not scored)"
    assert result["completeness"]["complete"] is False and result["completeness"]["unprocessed"][0]["item"] == "CDHDR"


def test_a_model_that_only_just_fits_the_parts_still_scores_everything():
    result = score(TinyModel(max_output=60, window=8000), rows("CDHDR", 60))     # the parts fit; the playoff's answer does not
    assert result["unscored"] == [] and sum(1 for r in result["rows"] if r["Score"] != "") == 60
    assert result["recommended"][0]["Archiving Object"] != ""
    assert result["recommended"][0]["Rationale"].startswith("Highest score")      # (no model rationale, a plain sentence instead)
