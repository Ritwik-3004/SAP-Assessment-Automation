"""A simulated model layer with a configurable output limit and context window, for testing that every task is
complete (or says what is missing) whatever the model. No network, no quota.

    model = TinyModel(max_output=300, window=4000)
    model.handle(_Pairs, lambda system, user: _Pairs(pairs=[...]))     # what it answers for each schema
    with model.install():
        ...                                                          # code under test calls llm.structured as usual

An answer that needs more tokens than min(max_tokens asked, max_output) is cut off exactly as a real model's
would be (llm.LLMCutOffError); a request larger than the window is refused (llm.LLMTooLargeError).
"""

import contextlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import llm  # noqa: E402


class TinyModel:
    def __init__(self, max_output: int = 300, window: int = 4000, chars_per_token: float = 3.5):
        self.max_output = max_output
        self.window = window
        self.cpt = chars_per_token
        self.handlers: dict = {}
        self.calls: list[dict] = []

    def tokens(self, text: str) -> int:
        return int(len(text) / self.cpt) + 1

    def handle(self, schema, fn) -> None:
        self.handlers[schema] = fn

    def structured(self, system, user, schema, max_tokens=1024, settings=None):
        allowed = min(max_tokens, self.max_output)
        need_in = self.tokens(system) + self.tokens(user)
        self.calls.append({"schema": schema.__name__, "in_tokens": need_in, "system": system, "user": user})
        if need_in + allowed > self.window:
            raise llm.LLMTooLargeError(f"The request is about {need_in} tokens, more than the {self.window}-token window.")
        answer = self.handlers[schema](system, user)
        text = answer.model_dump_json()
        if self.tokens(text) > allowed:
            raise llm.LLMCutOffError(llm.CUT_OFF_MESSAGE, partial=text[: int(allowed * self.cpt)])
        return answer

    @contextlib.contextmanager
    def install(self):
        settings = llm.Settings(provider="groq", model="tiny-model", groq_api_key="x")
        saved = {n: getattr(llm, n) for n in ("load_settings", "not_configured_message", "max_output_tokens", "structured")}
        llm.load_settings = lambda: settings
        llm.not_configured_message = lambda s=None: None
        llm.max_output_tokens = lambda s=None: self.max_output
        llm.structured = self.structured
        try:
            yield self
        finally:
            for n, v in saved.items():
                setattr(llm, n, v)


def excel_bytes(rows: list[list], sheet: str = "Sheet1") -> bytes:
    """An .xlsx workbook with *rows* on one sheet, as bytes (the way an upload arrives)."""
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
