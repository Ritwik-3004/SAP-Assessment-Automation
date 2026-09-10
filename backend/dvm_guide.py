"""
Per-table reference material from SAP's official "Data Management Guide for
SAP Business Suite" (DVM best-practice PDF, in resources/DVM_Guide.pdf), used
to ground archiving-object scoring in scoring.py against SAP's own guidance
instead of the model's unaided recall.

The guide is organized as one numbered section per table or table group
(e.g. "5.13 E070, E071, E071K: Change and Transport System"), each with an
"Archiving" subsection that names the recommended archiving object(s) where
applicable -- exactly the grounding scoring.py needs, keyed by the same
table name already present in every scoring row.

Parsed once (a few hundred ms for the whole ~225-page PDF) and cached to a
JSON file next to the PDF so repeated backend restarts during development
don't re-parse it every time; the cache is rebuilt automatically if the PDF
is newer than the cache.
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "resources")
PDF_PATH = os.path.join(_RESOURCES_DIR, "DVM_Guide.pdf")
_CACHE_PATH = os.path.join(_RESOURCES_DIR, "dvm_guide_index.json")

# Bounds how much of one table's section gets fed into a single scoring
# prompt. Most sections are far shorter (average ~3.7K chars); this only
# clips the handful of unusually long ones (e.g. IDoc tables).
MAX_EXCERPT_CHARS = 6000

# Body section headings look like "5.13 E070, E071, E071K: Change and
# Transport System". The same text also appears in the Table of Contents
# with a trailing page number (e.g. "...System 53") -- _is_toc_line tells
# the two apart so only real body headings are used as section boundaries.
_HEADING_RE = re.compile(r"^(\d+\.\d+)\s+([^:\n]+):\s*(.+)$", re.MULTILINE)


def _is_toc_line(description: str) -> bool:
    return bool(re.search(r"\d\s*$", description.strip()))


def _split_table_names(raw: str) -> list[str]:
    raw = raw.replace(" and ", ",")
    names = []
    for part in raw.split(","):
        part = part.strip(" *()").strip()
        part = re.sub(r"\*$", "", part).strip()
        if part and re.match(r"^[A-Z0-9_/]+$", part, re.IGNORECASE):
            names.append(part.upper())
    return names


def _extract_pdf_text(pdf_path: str) -> str:
    import pypdf

    reader = pypdf.PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages)


def _build_index(pdf_path: str) -> dict[str, str]:
    text = _extract_pdf_text(pdf_path)
    matches = list(_HEADING_RE.finditer(text))
    body_matches = [m for m in matches if not _is_toc_line(m.group(3))]

    index: dict[str, str] = {}
    for i, m in enumerate(body_matches):
        start = m.start()
        end = body_matches[i + 1].start() if i + 1 < len(body_matches) else len(text)
        section_text = text[start:end].strip()[:MAX_EXCERPT_CHARS]
        for table_name in _split_table_names(m.group(2)):
            index.setdefault(table_name, section_text)

    logger.info(
        "DVM guide index built: %d sections, %d distinct table names",
        len(body_matches), len(index),
    )
    return index


_index_cache: "dict[str, str] | None" = None


def load_index() -> dict[str, str]:
    """Load (or build and cache) the table-name -> guide-excerpt index.
    Returns {} if the PDF isn't present -- scoring falls back to unaided
    model knowledge rather than failing."""
    global _index_cache
    if _index_cache is not None:
        return _index_cache

    if not os.path.exists(PDF_PATH):
        logger.info("DVM guide PDF not found at %s -- scoring without it.", PDF_PATH)
        _index_cache = {}
        return _index_cache

    if os.path.exists(_CACHE_PATH) and os.path.getmtime(_CACHE_PATH) >= os.path.getmtime(PDF_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                _index_cache = json.load(f)
            return _index_cache
        except Exception:
            logger.warning("Could not read DVM guide index cache; rebuilding.")

    _index_cache = _build_index(PDF_PATH)
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_index_cache, f)
    except Exception:
        logger.warning("Could not write DVM guide index cache (non-fatal).")

    return _index_cache


def get_reference(table_name: str) -> "str | None":
    return load_index().get(table_name.upper())
