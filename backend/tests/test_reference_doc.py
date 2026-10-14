"""The reference-document review reads the whole document, however small the model's limits, and says so when it
cannot (Phase 3). A simulated model stands in for the real ones."""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import llm  # noqa: E402
import object_descriptions  # noqa: E402
import reference_doc as rd  # noqa: E402
from fakes import TinyModel, excel_bytes  # noqa: E402

ROW = re.compile(r"^([A-Z0-9_]+) \| ([A-Z0-9_]+)$", re.MULTILINE)


def pairs_from_text(system, user):
    """What a competent model would answer: one pair per 'KEY | VALUE' row of the text it was given."""
    return rd._Pairs(pairs=[rd._Pair(key=k, value=v) for k, v in ROW.findall(user)])


def sheet(n):
    return excel_bytes([[f"TAB{i:04d}", f"OBJ_{i:04d}"] for i in range(n)])


def recommended(n):
    return [{"Table Name": f"TAB{i:04d}", "Archiving Object": f"OBJ_{i:04d}"} for i in range(0, n, 10)]


def run_tables(model, n_rows):
    model.handle(rd._Pairs, pairs_from_text)
    saved = object_descriptions.resolve
    object_descriptions.resolve = lambda proposed, known=None: {}
    try:
        with model.install():
            return rd.analyze_reference_doc([(sheet(n_rows), "ref.xlsx")], recommended(n_rows))
    finally:
        object_descriptions.resolve = saved


def test_a_long_sheet_is_read_completely_on_a_tiny_model():
    model = TinyModel(max_output=300)
    result = run_tables(model, 100)
    assert result["status"] == "ok"
    assert len(result["ref_mappings"]) == 100
    assert result["completeness"]["complete"] is True and result["warnings"] == []
    assert len(model.calls) > 1                       # it really was read in parts


def test_tiny_and_big_models_find_the_same_mappings():
    tiny, big = TinyModel(max_output=300), TinyModel(max_output=16000, window=200000)
    a, b = run_tables(tiny, 100), run_tables(big, 100)
    assert a["ref_mappings"] == b["ref_mappings"] and len(b["ref_mappings"]) == 100
    assert len(tiny.calls) > len(big.calls) == 1


def test_answers_that_are_cut_off_are_split_not_lost():
    # a model whose tokens are as long as characters: even a small part's answer does not fit and has to be halved
    model = TinyModel(max_output=100, chars_per_token=1.0, window=100000)
    result = run_tables(model, 60)
    assert len(result["ref_mappings"]) == 60 and result["completeness"]["complete"] is True
    assert any(c["schema"] == "_Pairs" for c in model.calls) and len(model.calls) > 10


def test_what_is_beyond_the_part_limit_is_reported_not_dropped_silently():
    model = TinyModel(max_output=300)
    result = run_tables(model, 400)                   # more rows than MAX_PARTS parts of this model can read
    c = result["completeness"]
    assert c["complete"] is False and c["trimmed"] and c["trimmed"][0]["what"] == "ref.xlsx"
    assert 0 < len(result["ref_mappings"]) < 400
    assert any("ref.xlsx: only" in w and "could be sent to the model" in w for w in result["warnings"])


def test_nothing_readable_is_an_error_not_an_empty_result():
    model = TinyModel(max_output=300)

    def never_fits(system, user):
        raise llm.LLMCutOffError("cut off")

    model.handle(rd._Pairs, never_fits)
    with model.install():
        result = rd.analyze_reference_doc([(sheet(30), "ref.xlsx")], recommended(30))
    assert result["status"] == "error" and "ref.xlsx" in result["message"]


def test_a_limit_stop_part_way_keeps_what_was_read_and_says_so():
    model = TinyModel(max_output=300)
    seen = []

    def stops_on_third(system, user):
        seen.append(1)
        if len(seen) == 3:
            raise llm.LLMRateLimitError("Groq limit reached", daily=True)
        return pairs_from_text(system, user)

    model.handle(rd._Pairs, stops_on_third)
    saved = object_descriptions.resolve
    object_descriptions.resolve = lambda proposed, known=None: {}
    try:
        with model.install():
            result = rd.analyze_reference_doc([(sheet(100), "ref.xlsx")], recommended(100))
    finally:
        object_descriptions.resolve = saved
    assert result["status"] == "ok" and 0 < len(result["ref_mappings"]) < 100
    assert result["completeness"]["complete"] is False
    assert any("not processed" in w and "Groq limit reached" in w for w in result["warnings"])


def test_header_table_reference_is_read_completely_too():
    model = TinyModel(max_output=300)
    model.handle(rd._Pairs, pairs_from_text)
    rows = [{"Archiving Object": f"OBJ_{i:04d}", "Header Table": f"TAB{i:04d}"} for i in range(0, 90, 9)]
    data = excel_bytes([[f"OBJ_{i:04d}", f"TAB{i:04d}"] for i in range(90)])
    with model.install():
        result = rd.analyze_header_reference([(data, "hdr.xlsx")], rows)
    assert result["status"] == "ok" and len(result["ref_mappings"]) == 90
    assert result["completeness"]["complete"] is True and len(result["matches"]) == len(rows)


def test_a_document_with_one_endless_line_is_still_read_in_parts():
    text = " ".join(f"TAB{i:04d} | OBJ_{i:04d}" for i in range(60))      # a PDF page with no line breaks
    model = TinyModel(max_output=300)
    seen = []

    def per_part(system, user):
        seen.append(len(user))
        return rd._Pairs(pairs=[rd._Pair(key=k, value=v) for k, v in re.findall(r"([A-Z0-9_]+) \| ([A-Z0-9_]+)", user)])

    model.handle(rd._Pairs, per_part)
    with model.install():
        found = rd._extract_mappings_via_llm(text * 1, ["TAB0001"], "scan.pdf")
    assert len(found) >= 55          # (a mapping cut in two by a part boundary may be lost; see the note in the plan)
