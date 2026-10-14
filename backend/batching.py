"""
Split-and-merge for every step that asks a model about many items, or about a long text.

Whatever the model, one request can only hold so much input and can only carry so much answer. Instead of
trusting that a request fits, these helpers make every step work in parts and never lose a part silently:

  * run_batched  -- a list of items. Asks for as many as fit; when an answer is cut off (or cannot be read) the
                    batch is split in two and asked again; items the model left out are asked for once more;
                    whatever still cannot be done is returned in ``missing`` with the reason.
  * run_text_parts -- a long text. Reads it part by part (a part whose answer was cut off is split in two), in
                    document order, up to a stated number of parts (anything beyond that is recorded as not read).

The caller supplies ``ask`` -- one model call for one batch/part, returning the answers it got -- so the prompt,
the schema and the answer handling stay with the step that knows them. A rate-limit stop, a rejected key or an
unconfigured model (llm.LLMRateLimitError, LLMAuthError, LLMNotConfiguredError) ends the work at once; what was
finished is kept and the rest is reported as not processed, so the caller can show it and offer a retry.

scoring.py has its own, older version of this (it also compares the best candidates against each other, which
needs scoring-specific logic); this module serves the steps that have no such comparison.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import llm
from completeness import Tally

logger = logging.getLogger(__name__)

FATAL = (llm.LLMAuthError, llm.LLMNotConfiguredError, llm.LLMRateLimitError)
MIN_PART_CHARS = 150        # a single line (or a run of text) this short is not split any further


def _reason(exc: BaseException) -> str:
    if isinstance(exc, llm.LLMCutOffError):
        return "the model's answer was cut off by its output limit"
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------

def items_per_request(
    settings: "llm.Settings",
    out_tokens_per_item: int,
    *,
    overhead_out_tokens: int = 200,
    in_chars_per_item: int = 0,
    fixed_in_chars: int = 0,
    in_chars_limit: int = 0,
    cap: Optional[int] = None,
) -> int:
    """How many items one request can hold with the active model: limited by what its answer can carry
    (``out_tokens_per_item`` each, after ``overhead_out_tokens`` of wrapper) and, if ``in_chars_limit`` is given,
    by the input (``in_chars_per_item`` each after ``fixed_in_chars``). At least 1, at most ``cap``."""
    fits = (llm.max_output_tokens(settings) - overhead_out_tokens) // max(1, out_tokens_per_item)
    if in_chars_limit and in_chars_per_item:
        fits = min(fits, (in_chars_limit - fixed_in_chars) // in_chars_per_item)
    if cap:
        fits = min(fits, cap)
    return max(1, fits)


def chunk_text(text: str, size: int, max_lines: Optional[int] = None) -> list[str]:
    """Split *text* into pieces of at most *size* characters (and, if given, at most *max_lines* lines -- a sheet of
    short rows needs a line limit because the answer grows with the number of rows), on line breaks where possible.
    A line longer than *size* (text with no line breaks, e.g. from a PDF) is cut at a space, or in the middle of a
    word as a last resort, so no piece is ever larger than *size*."""
    size = max(50, size)
    out: list[str] = []
    current: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            out.append("\n".join(current))
        current, length = [], 0

    for line in text.splitlines():
        while len(line) > size:
            cut = line.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            flush()
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        if current and (length + len(line) + 1 > size or (max_lines and len(current) >= max_lines)):
            flush()
        current.append(line)
        length += len(line) + 1
    flush()
    return [p for p in out if p.strip()]


# ---------------------------------------------------------------------------
# A list of items
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    results: dict = field(default_factory=dict)         # key -> what the model answered for it
    missing: list = field(default_factory=list)         # [(item, reason)] that could not be done
    fatal: Optional[BaseException] = None               # a limit/auth/config error that ended the work early
    calls: int = 0                                      # model calls made (for progress and tests)


def _pack(items: list, size: int, weight: Optional[Callable[[Any], int]], max_weight: Optional[int]) -> list[list]:
    """The first requests: consecutive items, at most *size* each and, with a weight, at most *max_weight* in total
    (an item heavier than that goes alone)."""
    batches: list[list] = []
    current: list = []
    used = 0
    for item in items:
        w = weight(item) if weight else 0
        if current and (len(current) >= size or (max_weight and used + w > max_weight)):
            batches.append(current)
            current, used = [], 0
        current.append(item)
        used += w
    if current:
        batches.append(current)
    return batches


def run_batched(
    items: list,
    ask: Callable[[list], dict],
    *,
    key: Callable[[Any], Any] = lambda item: item,
    size: int,
    weight: Optional[Callable[[Any], int]] = None,
    max_weight: Optional[int] = None,
    retry_missing: bool = True,
    on_batch: Optional[Callable[[int, int], None]] = None,
) -> BatchResult:
    """Ask the model about *items*, *size* at a time (and, if *weight* and *max_weight* are given, no more per
    request than *max_weight* in total, e.g. characters of text). ``ask(batch)`` makes ONE model call and returns
    {key(item): answer} for the items it answered (it may raise, e.g. llm.LLMCutOffError). A batch whose call
    fails is split in two and asked again, down to single items; items the model left out of an answer are asked
    for once more. ``on_batch(done, total_items)`` is called after each call with the items handled so far."""
    out = BatchResult()
    done_keys: set = set()
    total = len(items)
    size = max(1, size)

    def report() -> None:
        if on_batch:
            on_batch(len(done_keys) + len(out.missing), total)

    def give_up(batch: list, reason: str) -> None:
        for item in batch:
            out.missing.append((item, reason))
        report()

    def call(batch: list) -> dict:
        """One call: the answers for the asked-for items only (it raises if the call failed)."""
        out.calls += 1
        answers = ask(batch)
        wanted = {key(i) for i in batch}
        return {k: v for k, v in (answers or {}).items() if k in wanted}

    def process(batch: list, may_retry: bool) -> None:
        if out.fatal is not None:
            give_up(batch, f"not processed: {_reason(out.fatal)}")
            return
        try:
            got = call(batch)
        except FATAL as exc:
            out.fatal = exc
            give_up(batch, f"not processed: {_reason(exc)}")
            return
        except Exception as exc:
            if len(batch) == 1:
                logger.warning("Could not process %r: %s", key(batch[0]), exc)
                give_up(batch, _reason(exc))
                return
            logger.info("Splitting a batch of %d items after: %s", len(batch), exc)
            half = len(batch) // 2
            process(batch[:half], may_retry)
            process(batch[half:], may_retry)
            return

        if not got and len(batch) > 1:
            # an answer with none of the asked-for items is as good as unreadable: ask in smaller parts
            half = len(batch) // 2
            process(batch[:half], may_retry)
            process(batch[half:], may_retry)
            return
        for item in batch:
            if key(item) in got:
                out.results[key(item)] = got[key(item)]
                done_keys.add(key(item))
        absent = [i for i in batch if key(i) not in got]
        report()
        if absent and may_retry and retry_missing and got:
            process(absent, False)              # asked once more; if still absent it is reported as missing
        elif absent:
            give_up(absent, "the model left it out of its answer")

    for batch in _pack(items, size, weight, max_weight):
        process(batch, True)
    return out


# ---------------------------------------------------------------------------
# A long text
# ---------------------------------------------------------------------------

def _halves(part: str) -> list[str]:
    """*part* in two, on a line boundary near the middle (so a row of a sheet is never cut); a single long line
    is cut at a space."""
    lines = part.splitlines()
    if len(lines) > 1:
        target, total = len(part) // 2, 0
        for i, line in enumerate(lines[:-1], start=1):
            total += len(line) + 1
            if total >= target:
                return ["\n".join(lines[:i]), "\n".join(lines[i:])]
        return ["\n".join(lines[:-1]), lines[-1]]
    return chunk_text(part, len(part) // 2 + 1)


@dataclass
class TextResult:
    results: list = field(default_factory=list)         # what each part gave, in document order
    missing: list = field(default_factory=list)         # [(label, reason)] for parts that could not be read
    fatal: Optional[BaseException] = None
    parts_read: int = 0
    parts_total: int = 0
    calls: int = 0


def run_text_parts(
    text: str,
    ask: Callable[[str], Any],
    *,
    size: int,
    max_parts: int,
    max_lines: Optional[int] = None,
    what: str = "document",
    tally: Optional[Tally] = None,
    on_part: Optional[Callable[[int, int], None]] = None,
) -> TextResult:
    """Read *text* in parts of at most *size* characters and *max_lines* lines (at most *max_parts* of them; the
    rest is recorded in *tally* as not read). ``ask(part)`` makes ONE model call about one part and returns what it found; a part
    whose call fails is split in two and read again. ``on_part(done, total)`` reports progress."""
    parts = chunk_text(text, size, max_lines)
    out = TextResult(parts_total=len(parts))
    if len(parts) > max_parts:
        kept_chars = sum(len(p) for p in parts[:max_parts])
        if tally is not None:
            tally.trim(what, kept_chars, len(text))
        parts = parts[:max_parts]

    def read(part: str, label: str) -> None:
        if out.fatal is not None:
            out.missing.append((label, f"not processed: {_reason(out.fatal)}"))
            return
        try:
            out.calls += 1
            found = ask(part)
        except FATAL as exc:
            out.fatal = exc
            out.missing.append((label, f"not processed: {_reason(exc)}"))
            return
        except Exception as exc:
            if len(part) <= MIN_PART_CHARS and "\n" not in part:
                logger.warning("Could not read %s: %s", label, exc)
                out.missing.append((label, _reason(exc)))
                return
            logger.info("Splitting %s (%d characters) after: %s", label, len(part), exc)
            for n, piece in enumerate(_halves(part), start=1):
                read(piece, f"{label}, half {n}")
            return
        out.results.append(found)

    for n, part in enumerate(parts, start=1):
        read(part, f"{what}, part {n}")
        out.parts_read = n
        if on_part:
            on_part(n, len(parts))
    if tally is not None:
        for label, reason in out.missing:
            tally.miss(label, reason)
    return out
