"""The reference check for Archiving Object Analysis reads the whole document and compares every condition, however
small the model's limits; what it cannot do is listed, never shown as a gap (Phase 3). Simulated models only."""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import llm  # noqa: E402
import object_reference as orf  # noqa: E402
from fakes import TinyModel, excel_bytes  # noqa: E402

SETTINGS = llm.Settings(provider="groq", model="tiny-model", groq_api_key="x")
ROW = re.compile(r"^(OBJ_\d{3}) \| (.+)$", re.MULTILINE)


def read_handler(system, user):
    """One object per row 'OBJ_001 | condition text'."""
    return orf._RefObjects(objects=[orf._RefObject(object=o, conditions=[c]) for o, c in ROW.findall(user)])


def doc(n):
    return excel_bytes([[f"OBJ_{i:03d}", f"Residence time {i} days must have passed"] for i in range(n)])


def extract(model, n, name="ref.xlsx"):
    model.handle(orf._RefObjects, read_handler)
    with model.install():
        return orf.extract_reference([(doc(n), name)], [], SETTINGS)


def test_a_long_document_is_read_completely_on_a_tiny_model():
    tiny, big = TinyModel(max_output=300), TinyModel(max_output=16000, window=200000)
    a, b = extract(tiny, 120), extract(big, 120)
    assert len(a["objects"]) == 120 and a["objects"] == b["objects"]
    assert a["completeness"]["complete"] is True and a["warnings"] == []
    assert len(tiny.calls) > len(big.calls)


def test_what_is_beyond_the_part_limit_is_reported():
    result = extract(TinyModel(max_output=300), 1500)
    c = result["completeness"]
    assert c["complete"] is False and c["trimmed"][0]["what"] == "ref.xlsx"
    assert 0 < len(result["objects"]) < 1500
    assert any("ref.xlsx: only" in w for w in result["warnings"])


def test_a_part_that_is_cut_off_is_split():
    # room for the answer about one row (about 110 tokens) but not for two: every part has to be split down to single rows
    model = TinyModel(max_output=160, chars_per_token=1.0, window=100000)
    result = extract(model, 40)
    assert len(result["objects"]) == 40 and result["completeness"]["complete"] is True


def test_a_document_the_model_cannot_read_is_skipped_with_a_warning():
    model = TinyModel(max_output=300)

    def never(system, user):
        raise llm.LLMCutOffError("cut off")

    model.handle(orf._RefObjects, never)
    with model.install():
        result = orf.extract_reference([(doc(30), "bad.xlsx")], [], SETTINGS)
    assert result["used"] == [] and result["objects"] == {}
    assert any("bad.xlsx: the model could not read it" in w for w in result["warnings"])


def test_a_limit_stop_while_reading_is_raised_as_before():
    model = TinyModel(max_output=300)

    def limit(system, user):
        raise llm.LLMRateLimitError("Groq limit reached", daily=True)

    model.handle(orf._RefObjects, limit)
    with model.install():
        try:
            orf.extract_reference([(doc(30), "ref.xlsx")], [], SETTINGS)
        except llm.LLMRateLimitError:
            return
    raise AssertionError("a daily limit before anything was read must stop the step")


# ---------------------------------------------------------------- comparing conditions

def review_inputs(n_doc, n_tool=3):
    res = {
        "object": "OBJ_A",
        "prerequisites": [],
        "conditions": [{"condition": f"Tool condition {i}", "source": "SARA information page"} for i in range(1, n_tool + 1)],
    }
    ref = {
        "objects": {"OBJ_A": {"conditions": [{"text": f"Document condition {i}", "file": "ref.xlsx"} for i in range(1, n_doc + 1)],
                              "archive_before": []}},
        "used": ["ref.xlsx"],
    }
    return [res], ref


def match_handler(system, user):
    numbers = [int(n) for n in re.findall(r"^(\d+)\. ", user.split("DOCUMENT conditions:")[1], re.MULTILINE)]
    return orf._Matches(items=[orf._Match(doc_index=n, status="covered", tool_index=1) for n in numbers])


def compare(model, results, ref):
    with model.install():
        return orf.compare(SETTINGS, results, ref)


def test_many_conditions_are_all_compared_on_a_tiny_model():
    model = TinyModel(max_output=300)
    model.handle(orf._Matches, match_handler)
    out = compare(model, *review_inputs(30))
    c = out["objects"][0]["conditions"]
    assert len(c["covered"]) == 30 and c["new"] == [] and c["not_compared"] == []
    assert out["summary"]["not_compared"] == 0 and out["completeness"]["complete"] is True
    assert len(model.calls) > 1


def test_a_condition_that_cannot_be_compared_is_not_called_a_gap():
    model = TinyModel(max_output=300)

    def fails_on_condition_5(system, user):
        if re.search(r"^5\. ", user.split("DOCUMENT conditions:")[1], re.MULTILINE):
            raise llm.LLMCutOffError("cut off")
        return match_handler(system, user)

    model.handle(orf._Matches, fails_on_condition_5)
    out = compare(model, *review_inputs(12))
    c = out["objects"][0]["conditions"]
    assert [n["text"] for n in c["not_compared"]] == ["Document condition 5"]
    assert "cut off" in c["not_compared"][0]["reason"]
    assert len(c["covered"]) == 11 and c["new"] == []           # (the old code would have shown all as gaps)
    assert out["completeness"]["complete"] is False and out["summary"]["not_compared"] == 1


def test_a_condition_the_model_leaves_out_is_not_called_a_gap():
    model = TinyModel(max_output=300)

    def skips_3(system, user):
        answer = match_handler(system, user)
        return orf._Matches(items=[m for m in answer.items if m.doc_index != 3])

    model.handle(orf._Matches, skips_3)
    c = compare(model, *review_inputs(6))["objects"][0]["conditions"]
    assert [n["text"] for n in c["not_compared"]] == ["Document condition 3"] and c["new"] == []


def test_a_verdict_without_a_usable_tool_number_is_not_called_a_gap():
    model = TinyModel(max_output=300)
    model.handle(orf._Matches, lambda s, u: orf._Matches(items=[orf._Match(doc_index=1, status="covered", tool_index=9)]))
    c = compare(model, *review_inputs(1))["objects"][0]["conditions"]
    assert c["covered"] == [] and c["new"] == [] and len(c["not_compared"]) == 1


def test_after_a_limit_stop_nothing_more_is_asked_and_the_rest_is_reported():
    model = TinyModel(max_output=300)
    calls = []

    def stops_on_second(system, user):
        calls.append(1)
        if len(calls) == 2:
            raise llm.LLMRateLimitError("Groq limit reached", daily=True)
        return match_handler(system, user)

    model.handle(orf._Matches, stops_on_second)
    res_a, ref = review_inputs(12)
    res_b = {**res_a[0], "object": "OBJ_B"}
    ref["objects"]["OBJ_B"] = ref["objects"]["OBJ_A"]
    out = compare(model, [res_a[0], res_b], ref)
    a, b = (o["conditions"] for o in out["objects"])
    assert len(a["covered"]) == 4 and len(a["not_compared"]) == 8          # the first batch of 4 was done
    assert len(b["not_compared"]) == 12 and b["covered"] == []
    assert len(calls) == 2                                               # nothing was asked after the stop
    assert all("not processed" in n["reason"] and "Groq limit reached" in n["reason"] for n in a["not_compared"] + b["not_compared"])


def test_nothing_to_compare_with_still_means_new():
    model = TinyModel(max_output=300)
    model.handle(orf._Matches, match_handler)
    res, ref = review_inputs(3, n_tool=0)
    c = compare(model, res, ref)["objects"][0]["conditions"]
    assert len(c["new"]) == 3 and c["not_compared"] == [] and model.calls == []


def test_the_extraction_warnings_carry_into_the_review():
    model = TinyModel(max_output=300)
    model.handle(orf._Matches, match_handler)
    res, ref = review_inputs(2)
    ref["completeness"] = {"complete": False, "trimmed": [{"what": "ref.xlsx", "kept": 10, "total": 99, "unit": "characters"}],
                           "unprocessed": [], "notes": []}
    out = compare(model, res, ref)
    assert out["completeness"]["complete"] is False and out["completeness"]["trimmed"][0]["what"] == "ref.xlsx"
