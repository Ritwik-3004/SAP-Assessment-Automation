"""The split-and-merge routine finishes every item or reports exactly what it could not do (Phase 2).
A simulated model with a small output limit stands in for the real ones."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import batching  # noqa: E402
import llm  # noqa: E402
from completeness import Tally  # noqa: E402


class SmallModel:
    """Answers {item: item*2} for a batch, but its answer only has room for *room* items: a bigger batch is cut
    off (like a real model that hits its output limit)."""

    def __init__(self, room=4, omit=(), fail_on_call=None, fail_with=None):
        self.room, self.omit, self.calls, self.sizes = room, set(omit), 0, []
        self.fail_on_call, self.fail_with = fail_on_call, fail_with

    def __call__(self, batch):
        self.calls += 1
        self.sizes.append(len(batch))
        if self.fail_on_call == self.calls:
            raise self.fail_with
        if len(batch) > self.room:
            raise llm.LLMCutOffError("cut off", partial="{")
        return {i: i * 2 for i in batch if i not in self.omit}


def test_everything_fits_in_one_call():
    model = SmallModel(room=10)
    r = batching.run_batched(list(range(8)), model, size=8)
    assert r.results == {i: i * 2 for i in range(8)} and r.missing == [] and model.calls == 1


def test_batches_that_are_cut_off_are_split_until_they_fit():
    model = SmallModel(room=4)
    r = batching.run_batched(list(range(37)), model, size=37)       # asked far too much at once
    assert r.results == {i: i * 2 for i in range(37)}
    assert r.missing == [] and r.fatal is None
    assert max(model.sizes[1:]) <= 37 and min(model.sizes) >= 1
    assert model.calls > 1


def test_items_are_taken_size_at_a_time():
    model = SmallModel(room=100)
    batching.run_batched(list(range(25)), model, size=10)
    assert model.sizes == [10, 10, 5]


def test_items_the_model_left_out_are_asked_for_once_more():
    seen = []

    def ask(batch):
        seen.append(list(batch))
        return {i: i for i in batch if not (len(seen) == 1 and i >= 3)}   # first answer stops after 3 items

    r = batching.run_batched(list(range(6)), ask, size=6)
    assert r.results == {i: i for i in range(6)} and r.missing == []
    assert seen == [[0, 1, 2, 3, 4, 5], [3, 4, 5]]


def test_an_item_the_model_never_answers_is_reported_not_guessed():
    model = SmallModel(room=10, omit=[2])
    r = batching.run_batched(list(range(5)), model, size=5)
    assert sorted(r.results) == [0, 1, 3, 4]
    assert r.missing == [(2, "the model left it out of its answer")]


def test_a_batch_with_no_usable_answer_is_split():
    calls = []

    def ask(batch):
        calls.append(len(batch))
        return {} if len(batch) > 2 else {i: i for i in batch}      # a big batch gives nothing usable

    r = batching.run_batched(list(range(6)), ask, size=6)
    assert r.results == {i: i for i in range(6)} and r.missing == []


def test_a_single_item_that_always_fails_is_reported_with_the_reason():
    def ask(batch):
        if 3 in batch:
            raise llm.LLMCutOffError("cut off")
        return {i: i for i in batch}

    r = batching.run_batched(list(range(6)), ask, size=6)
    assert sorted(r.results) == [0, 1, 2, 4, 5]
    assert r.missing == [(3, "the model's answer was cut off by its output limit")]


def test_a_limit_stop_keeps_finished_work_and_reports_the_rest():
    model = SmallModel(room=3, fail_on_call=3, fail_with=llm.LLMRateLimitError("Groq limit reached", daily=True))
    r = batching.run_batched(list(range(9)), model, size=3)
    assert isinstance(r.fatal, llm.LLMRateLimitError)
    assert r.results == {i: i * 2 for i in range(6)}                        # the first two batches were finished
    assert [i for i, _ in r.missing] == [6, 7, 8]
    assert all("not processed" in reason and "Groq limit reached" in reason for _, reason in r.missing)
    assert model.calls == 3                                                 # nothing was asked after the stop


def test_progress_counts_handled_items():
    seen = []
    model = SmallModel(room=100)
    batching.run_batched(list(range(25)), model, size=10, on_batch=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (25, 25) and [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_no_items_means_no_calls():
    model = SmallModel()
    assert batching.run_batched([], model, size=5).results == {} and model.calls == 0


def test_a_key_function_is_used_to_match_answers():
    r = batching.run_batched([{"id": "A"}, {"id": "B"}], lambda b: {i["id"]: 1 for i in b}, key=lambda i: i["id"], size=2)
    assert r.results == {"A": 1, "B": 1}


# ---------------------------------------------------------------- sizing

def test_items_per_request_follows_what_the_answer_can_carry():
    old = llm.max_output_tokens
    try:
        llm.max_output_tokens = lambda settings=None: 1000
        s = llm.Settings(provider="groq", model="m", groq_api_key="x")
        assert batching.items_per_request(s, 20, overhead_out_tokens=200) == 40
        assert batching.items_per_request(s, 20, overhead_out_tokens=200, cap=10) == 10
        assert batching.items_per_request(s, 20, in_chars_per_item=100, fixed_in_chars=500, in_chars_limit=1500) == 10
        assert batching.items_per_request(s, 5000) == 1             # never less than one item
    finally:
        llm.max_output_tokens = old


# ---------------------------------------------------------------- text

def test_chunk_text_never_exceeds_the_size_and_keeps_all_text():
    text = "\n".join(f"line {i} " + "x" * (i % 40) for i in range(200))
    pieces = batching.chunk_text(text, 300)
    assert all(len(p) <= 300 for p in pieces)
    assert "".join(pieces).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_chunk_text_cuts_a_very_long_line():
    text = ("word " * 1000).strip()          # one line of 4,999 characters, as a PDF may give
    pieces = batching.chunk_text(text, 500)
    assert len(pieces) >= 10 and all(len(p) <= 500 for p in pieces)
    assert " ".join(pieces).split() == text.split()
    nospace = "x" * 2000
    assert all(len(p) <= 500 for p in batching.chunk_text(nospace, 500)) and "".join(batching.chunk_text(nospace, 500)) == nospace


def test_text_parts_are_read_in_order_and_split_when_cut_off():
    text = "\n".join(f"row {i:04d}" for i in range(400))     # about 3,600 characters

    def ask(part):
        if len(part) > 700:
            raise llm.LLMCutOffError("cut off")
        return part.count("row")

    tally = Tally()
    r = batching.run_text_parts(text, ask, size=2000, max_parts=8, tally=tally)
    assert sum(r.results) == 400 and r.missing == [] and tally.complete


def test_text_beyond_the_part_limit_is_reported_as_not_read():
    text = "\n".join(f"row {i:04d}" for i in range(400))
    tally = Tally()
    r = batching.run_text_parts(text, lambda part: part.count("row"), size=500, max_parts=3, tally=tally)
    s = tally.summary()
    assert r.parts_total > 3 and r.parts_read == 3 and len(r.results) == 3
    assert s["trimmed"][0]["what"] == "document" and s["trimmed"][0]["kept"] < s["trimmed"][0]["total"]
    assert not s["complete"] and "only the first 3 of" in s["notes"][0]


def test_a_text_part_that_cannot_be_read_is_reported_and_the_rest_continues():
    text = "\n".join(f"row {i:04d}" for i in range(400))
    seen = []

    def ask(part):
        seen.append(part)
        if "row 0100" in part:
            raise llm.LLMCutOffError("cut off")
        return part.count("row")

    tally = Tally()
    r = batching.run_text_parts(text, ask, size=900, max_parts=20, tally=tally)
    assert r.missing and all(reason.startswith("the model's answer was cut off") for _, reason in r.missing)
    assert sum(r.results) > 300 and not tally.complete


def test_a_limit_stop_while_reading_text_keeps_what_was_read():
    text = "\n".join(f"row {i:04d}" for i in range(400))
    calls = []

    def ask(part):
        calls.append(1)
        if len(calls) == 3:
            raise llm.LLMRateLimitError("limit", daily=False)
        return 1

    r = batching.run_text_parts(text, ask, size=600, max_parts=20)
    assert len(r.results) == 2 and isinstance(r.fatal, llm.LLMRateLimitError) and len(calls) == 3
    assert r.missing and all("not processed" in reason for _, reason in r.missing)
