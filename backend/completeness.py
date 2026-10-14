"""
What a task could not do completely, so a result is never silently partial.

Every step that sends text to an AI model can run into the model's limits: an excerpt has to be shortened to fit the
window, an answer is cut off, a part of the work is refused. The code splits the work wherever it can (see
batching.py); whatever still could not be done, or had to be shortened, is recorded in a Tally. A task returns
``tally.summary()`` with its result, and the screen and the saved workbook say so.

    tally = Tally()
    excerpt = tally.shorten("DVM Guide excerpt for BKPF", excerpt, limit)      # records it if the text was cut
    tally.miss("CDHDR", "the model's answer was cut off")                       # something was not done
    result["completeness"] = tally.summary()
"""

import threading
from typing import Any


class Tally:
    """Collects what was shortened and what was not processed. Safe to use from several threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trimmed: dict[str, dict[str, Any]] = {}
        self._unprocessed: dict[str, str] = {}
        self._notes: list[str] = []

    # -- recording ------------------------------------------------------------

    def trim(self, what: str, kept: int, total: int, unit: str = "characters") -> None:
        """Something of *total* *unit*s was cut to *kept* before being sent to the model (nothing is recorded if
        it was not shortened). The same *what* recorded again keeps the largest cut."""
        if kept >= total:
            return
        with self._lock:
            old = self._trimmed.get(what)
            if old is None or total - kept > old["total"] - old["kept"]:
                self._trimmed[what] = {"what": what, "kept": kept, "total": total, "unit": unit}

    def shorten(self, what: str, text: str, limit: int) -> str:
        """*text* cut to *limit* characters; the cut is recorded."""
        if len(text) <= limit:
            return text
        self.trim(what, limit, len(text))
        return text[:limit]

    def miss(self, item: str, reason: str) -> None:
        """*item* could not be processed (the first reason given for an item is kept)."""
        with self._lock:
            self._unprocessed.setdefault(item, reason)

    def note(self, text: str) -> None:
        with self._lock:
            if text not in self._notes:
                self._notes.append(text)

    def merge(self, other: "Tally") -> None:
        for t in other.summary()["trimmed"]:
            self.trim(t["what"], t["kept"], t["total"], t["unit"])
        for u in other.summary()["unprocessed"]:
            self.miss(u["item"], u["reason"])
        for n in other.summary()["notes"]:
            self.note(n)

    # -- reading --------------------------------------------------------------

    @property
    def complete(self) -> bool:
        with self._lock:
            return not self._trimmed and not self._unprocessed

    def summary(self) -> dict:
        """{"complete": bool, "trimmed": [{what, kept, total, unit}], "unprocessed": [{item, reason}], "notes": [str]}.
        *complete* is False if anything was shortened or not processed."""
        with self._lock:
            return {
                "complete": not self._trimmed and not self._unprocessed,
                "trimmed": [dict(t) for t in self._trimmed.values()],
                "unprocessed": [{"item": i, "reason": r} for i, r in self._unprocessed.items()],
                "notes": list(self._notes),
            }

    def sentences(self, limit: int = 8) -> list[str]:
        """One short sentence per problem, for a warning list or a workbook note (at most *limit*, then a count)."""
        s = self.summary()
        lines = [
            f"{t['what']}: only {t['kept']:,} of {t['total']:,} {t['unit']} could be sent to the model."
            for t in s["trimmed"]
        ]
        lines += [f"{u['item']}: not processed ({u['reason']})." for u in s["unprocessed"]]
        lines += s["notes"]
        if len(lines) > limit:
            lines = lines[:limit] + [f"... and {len(lines) - limit} more."]
        return lines
