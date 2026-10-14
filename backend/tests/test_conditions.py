"""Condition extraction for Archiving Object Analysis keeps every condition of a long page, however small the
model's limits, and reports what it cannot read (Phase 3). Simulated models only."""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import llm  # noqa: E402
import object_analysis as oa  # noqa: E402
from completeness import Tally  # noqa: E402
from fakes import TinyModel  # noqa: E402

SETTINGS = llm.Settings(provider="groq", model="tiny-model", groq_api_key="x")
BLOCK = re.compile(r"^\[(\d+)\] .*?\n---\n(.*?)\n---$", re.MULTILINE | re.DOTALL)
CONDITION = re.compile(r"^Condition (\d+): .+$", re.MULTILINE)


def conditions_handler(system, user):
    """What a competent model would answer: one condition per 'Condition N: ...' line, tagged with its block."""
    items = []
    for number, text in BLOCK.findall(user):
        for line in CONDITION.findall(text):
            items.append(oa._Condition(condition=f"Condition {line} must hold", block=int(number)))
    return oa._Conditions(conditions=items)


def page(n, start=0):
    return "\n".join(f"Condition {i}: the status of the documents must be 'complete' before archiving." for i in range(start, start + n))


def extract(model, blocks, tally=None):
    model.handle(oa._Conditions, conditions_handler)
    with model.install():
        return oa.extract_conditions(SETTINGS, "FI_DOCUMNT", "Financial documents", blocks, tally)


def test_a_page_with_more_conditions_than_one_answer_can_carry_is_read_in_pieces():
    tally = Tally()
    model = TinyModel(max_output=300)
    found = extract(model, [{"label": "SARA - Checks", "text": page(60)}], tally)
    assert len(found) == 60 and tally.complete
    assert len(model.calls) > 1
    assert all(c["source"] == "SARA - Checks" for c in found)


def test_tiny_and_big_models_find_the_same_conditions():
    blocks = [{"label": "SARA - Checks", "text": page(60)}, {"label": "DVM Guide", "text": page(5, start=100)}]
    tiny, big = TinyModel(max_output=300), TinyModel(max_output=16000, window=200000)
    a, b = extract(tiny, blocks), extract(big, blocks)
    assert sorted(c["condition"] for c in a) == sorted(c["condition"] for c in b) and len(b) == 65
    assert len(big.calls) == 1


def test_each_condition_keeps_the_label_of_the_block_it_came_from():
    found = extract(TinyModel(max_output=16000, window=200000), [
        {"label": "A", "text": page(2)}, {"label": "B", "text": page(2, start=10)},
    ])
    assert {c["condition"]: c["source"] for c in found} == {
        "Condition 0 must hold": "A", "Condition 1 must hold": "A",
        "Condition 10 must hold": "B", "Condition 11 must hold": "B",
    }


def test_text_longer_than_the_part_limit_is_reported_not_dropped_silently():
    tally = Tally()
    long_text = page(1500)                                  # about 120,000 characters
    found = extract(TinyModel(max_output=16000, window=1000000), [{"label": "SARA - Long", "text": long_text}], tally)
    s = tally.summary()
    assert s["complete"] is False and s["trimmed"][0]["what"] == "FI_DOCUMNT: source text"
    assert 0 < len(found) < 1500


def test_a_part_that_cannot_be_read_is_reported_and_the_others_are_kept():
    model = TinyModel(max_output=300)

    def fails_on_bad(system, user):
        if "Condition 7:" in user:                 # the model can never answer about this one condition
            raise llm.LLMCutOffError("cut off")
        return conditions_handler(system, user)

    model.handle(oa._Conditions, fails_on_bad)
    tally = Tally()
    with model.install():
        found = oa.extract_conditions(SETTINGS, "FI_DOCUMNT", "", [{"label": "SARA", "text": page(12)}], tally)
    assert len(found) == 11 and "Condition 7 must hold" not in [c["condition"] for c in found]
    assert not tally.complete and any("cut off" in s for s in tally.sentences())


def test_nothing_readable_raises_so_the_source_is_marked_as_failed():
    model = TinyModel(max_output=300)

    def never(system, user):
        raise llm.LLMCutOffError("cut off")

    model.handle(oa._Conditions, never)
    with model.install():
        try:
            oa.extract_conditions(SETTINGS, "FI_DOCUMNT", "", [{"label": "SARA", "text": page(3)}])
        except llm.LLMError:
            return
    raise AssertionError("expected an LLMError")


def test_a_limit_stop_after_something_was_read_keeps_it_and_reports_the_rest():
    model = TinyModel(max_output=300)
    calls = []

    def stops_late(system, user):
        calls.append(1)
        if len(calls) >= 8:
            raise llm.LLMRateLimitError("Groq limit reached", daily=True)
        return conditions_handler(system, user)

    model.handle(oa._Conditions, stops_late)
    tally = Tally()
    with model.install():
        found = oa.extract_conditions(SETTINGS, "FI_DOCUMNT", "", [{"label": "SARA", "text": page(60)}], tally)
    assert found and len(found) < 60 and not tally.complete
    assert any("Groq limit reached" in s for s in tally.sentences())
    assert len(calls) == 8                       # nothing was asked after the stop


def test_a_limit_stop_before_anything_was_read_is_raised_as_a_limit_stop():
    model = TinyModel(max_output=300)

    def stops(system, user):
        raise llm.LLMRateLimitError("Groq limit reached", daily=True)

    model.handle(oa._Conditions, stops)
    with model.install():
        try:
            oa.extract_conditions(SETTINGS, "FI_DOCUMNT", "", [{"label": "SARA", "text": page(60)}])
        except llm.LLMRateLimitError:
            return
    raise AssertionError("expected the limit stop to be raised")


def test_no_blocks_means_no_conditions_and_no_calls():
    model = TinyModel()
    model.handle(oa._Conditions, conditions_handler)
    with model.install():
        assert oa.extract_conditions(SETTINGS, "X", "", []) == [] and model.calls == []
