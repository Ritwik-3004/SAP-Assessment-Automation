"""The AI fallback for object descriptions asks about every object, however many, and one failure does not cost the
others (Phase 3). Simulated models only."""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import llm  # noqa: E402
import object_descriptions as od  # noqa: E402
from fakes import TinyModel  # noqa: E402

SETTINGS = llm.Settings(provider="groq", model="tiny-model", groq_api_key="x")


def names_in(user):
    return re.findall(r"^- (\S+)$", user, re.MULTILINE)


def describe_all(system, user):
    return od._ObjectDescriptions(items=[od._ObjectDescription(archiving_object=n, description=f"Description of {n}") for n in names_in(user)])


def run(model, objects):
    with model.install():
        return od._from_ai(objects, SETTINGS)


def test_every_object_is_asked_about_not_just_the_first_forty():
    names = [f"OBJ_{i:03d}" for i in range(100)]
    model = TinyModel(max_output=300)
    model.handle(od._ObjectDescriptions, describe_all)
    found = run(model, names)
    assert sorted(found) == sorted(names) and len(model.calls) > 1


def test_an_object_the_model_does_not_recognise_is_not_asked_again():
    names = [f"OBJ_{i:03d}" for i in range(12)]
    model = TinyModel(max_output=16000, window=200000)

    def blank_for_odd(system, user):
        return od._ObjectDescriptions(items=[
            od._ObjectDescription(archiving_object=n, description="" if int(n[-1]) % 2 else f"Description of {n}") for n in names_in(user)
        ])

    model.handle(od._ObjectDescriptions, blank_for_odd)
    found = run(model, names)
    assert sorted(found) == [n for n in names if int(n[-1]) % 2 == 0]
    assert len(model.calls) == 1


def test_one_object_that_cannot_be_answered_does_not_cost_the_others():
    names = [f"OBJ_{i:03d}" for i in range(20)]
    model = TinyModel(max_output=300)

    def fails_on_13(system, user):
        if "OBJ_013" in user:
            raise llm.LLMCutOffError("cut off")
        return describe_all(system, user)

    model.handle(od._ObjectDescriptions, fails_on_13)
    found = run(model, names)
    assert sorted(found) == sorted(n for n in names if n != "OBJ_013")


def test_a_limit_stop_returns_what_was_found_so_far():
    names = [f"OBJ_{i:03d}" for i in range(30)]
    model = TinyModel(max_output=300)
    calls = []

    def stops_third(system, user):
        calls.append(1)
        if len(calls) == 3:
            raise llm.LLMRateLimitError("limit", daily=True)
        return describe_all(system, user)

    model.handle(od._ObjectDescriptions, stops_third)
    found = run(model, names)
    assert 0 < len(found) < 30 and len(calls) == 3
