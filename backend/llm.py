"""
Provider-neutral LLM access for the whole app.

Every AI step (scoring, housekeeping lookup, SAP for Me article extraction,
reference-document analysis, chat) calls structured()/text()/run_tool_loop()
here instead of using a provider SDK directly. Which provider and model is used
comes from one setting, chosen in the app's "AI Model" panel and saved to
llm_settings.json -- so switching it changes every step at once.

Providers:
  * "anthropic" -- Claude (SCORING_MODEL, default claude-haiku-4-5). Behaviour is
    the same as before this module existed.
  * "groq" -- a Groq free-tier model. The free tier is tight: 30 requests/min,
    8,000 tokens/min, 1,000 requests/day, 200,000 tokens/day per model. Groq mode
    therefore (a) paces itself with a rolling one-minute window, (b) retries
    per-minute 429s, (c) uses smaller prompt budgets (see budget()), and (d) raises
    a clear LLMRateLimitError(daily=True) when a daily limit is hit, so callers can
    stop with a message instead of quietly producing blank results.
  * "ollama" -- a model running on THIS computer through Ollama. Nothing is sent over
    the network: the address must be localhost (checked on every call), models that
    Ollama would run in its cloud are never offered, and "Auto" picks the best
    installed model that fits the computer's memory. No rate limits, but small
    prompt budgets and one request at a time, since local inference is slow.
"""

import copy
import ipaddress
import json
import logging
import re
import threading
import time
import types
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

from config import (
    ANTHROPIC_API_KEY,
    GROQ_API_KEY,
    LLM_SETTINGS_FILE,
    LLM_USAGE_FILE,
    OLLAMA_TIMEOUT,
    OLLAMA_URL,
    SCORING_MODEL,
)

logger = logging.getLogger(__name__)

CLAUDE = "anthropic"
GROQ = "groq"
OLLAMA = "ollama"
PROVIDERS = (CLAUDE, GROQ, OLLAMA)

# (model id, label). Kept in one place so adding/removing a model is a one-line change.
GROQ_MODELS = (
    ("openai/gpt-oss-120b", "GPT-OSS 120B"),
    ("openai/gpt-oss-20b", "GPT-OSS 20B"),
    ("qwen/qwen3.8-27b", "Qwen 3.8 27B"),
)
GROQ_MODEL_IDS = tuple(m[0] for m in GROQ_MODELS)

# Free-tier limits (per model, per organisation) from console.groq.com/docs/rate-limits.
GROQ_LIMITS = {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 200000}
TPM_HEADROOM = 0.85       # plan to use at most this share of the per-minute token budget
GROQ_MAX_OUT = 4000       # cap on completion tokens requested from Groq
MAX_RATE_RETRIES = 6

# Some free-tier models also have a cap on OUTPUT tokens per minute (OTPM) that is not in Groq's general limits table:
# Qwen 3.8 27B answered "Request too large ... on output tokens per minute (OTPM): Limit 1000". A limit learned from a
# refusal (_learned_otpm) wins over this table, so a model that is not listed here teaches itself on its first refusal.
GROQ_MODEL_LIMITS = {"qwen/qwen3.8-27b": {"otpm": 1000}}
_learned_otpm: dict[str, int] = {}
# Live check (Qwen 3.8 27B, 2026-10-13): two back-to-back requests that each asked for 900 output tokens (limit 1,000) and
# used 2 were both admitted, so Groq counts the tokens actually produced. Only the request being sent has to fit; once it
# is answered the minute is charged its real output. Set True to charge the full requested amount instead.
OTPM_COUNTS_REQUESTED = False
# Models that refused the "reasoning off" parameter (see _groq_chat); it is not sent to them again.
_reasoning_unsupported: set[str] = set()

OLLAMA_NUM_CTX = 8192      # context window requested from Ollama (its own default, 4,096, is too small here)
OLLAMA_MAX_OUT = 3000      # cap on tokens generated per answer
OLLAMA_RAM_SHARE = 0.6     # a model's file must fit in this share of the computer's memory (CPU inference)
OLLAMA_MIN_USEFUL_B = 3.0  # below this many billion parameters a model is too small for this tool's JSON steps
OLLAMA_CACHE_SECONDS = 15
# Models worth pulling (name, approximate download in GB), best first -- only those that fit are suggested.
OLLAMA_SUGGESTED = (("qwen3:14b", 9.3), ("qwen3:8b", 5.2), ("llama3.1:8b", 4.9), ("qwen3:4b", 2.6))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class; str(exc) is always safe to show to the user."""


class LLMNotConfiguredError(LLMError):
    pass


class LLMAuthError(LLMError):
    pass


class LLMBadRequestError(LLMError):
    pass


class LLMTooLargeError(LLMError):
    pass


class LLMCutOffError(LLMError):
    """The model stopped because it reached its output limit (or its answer could not be read because it was cut
    off). *partial* is whatever text had been written. Callers that work in parts split the work and ask again."""

    def __init__(self, message: str, partial: str = ""):
        super().__init__(message)
        self.partial = partial


class LLMRateLimitError(LLMError):
    def __init__(self, message: str, daily: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.daily = daily
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Settings (which provider/model is active)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    groq_api_key: str = ""
    ollama_auto: bool = False   # Ollama: the model was picked automatically (best installed)


def _read_settings_file() -> dict:
    if LLM_SETTINGS_FILE.exists():
        try:
            return json.loads(LLM_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Could not read %s; using defaults.", LLM_SETTINGS_FILE)
    return {}


def load_settings() -> Settings:
    data = _read_settings_file()
    provider = data.get("provider") if data.get("provider") in PROVIDERS else CLAUDE
    auto = False
    if provider == GROQ:
        model = data.get("model") if data.get("model") in GROQ_MODEL_IDS else GROQ_MODEL_IDS[0]
    elif provider == OLLAMA:
        model = str(data.get("model") or "").strip()
        if not model:   # "Auto": the best model installed right now
            auto = True
            model = _best_local_model()
    else:
        model = SCORING_MODEL
    # The Groq key comes only from backend/.env (GROQ_API_KEY) -- the app never stores it.
    return Settings(provider, model, (GROQ_API_KEY or "").strip(), auto)


def save_settings(provider: str, model: str) -> Settings:
    """Validate and persist the provider/model selection. API keys are not handled
    here: they are read from backend/.env only. For Ollama, *model* "" means Auto."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'.")
    if provider == GROQ and model not in GROQ_MODEL_IDS:
        raise ValueError(f"Unsupported Groq model '{model}'.")
    if provider == GROQ:
        stored_model = model
    elif provider == OLLAMA:
        stored_model = (model or "").strip()
        if stored_model:
            if _is_cloud_name(stored_model):
                raise ValueError(f"'{stored_model}' runs in Ollama's cloud, not on this computer. Pick a local model.")
            try:
                info = ollama_models(refresh=True)
            except LLMError as exc:
                raise ValueError(str(exc)) from exc
            if info["reachable"] and stored_model not in [m["id"] for m in info["models"]]:
                raise ValueError(f"'{stored_model}' is not installed locally. Run: ollama pull {stored_model}")
    else:
        stored_model = SCORING_MODEL
    LLM_SETTINGS_FILE.write_text(
        json.dumps({"provider": provider, "model": stored_model}, indent=2), encoding="utf-8"
    )
    _daily_block.clear()
    return load_settings()


def label(settings: Optional[Settings] = None) -> str:
    s = settings or load_settings()
    if s.provider == GROQ:
        name = dict(GROQ_MODELS).get(s.model, s.model)
        return f"Groq · {name}"
    if s.provider == OLLAMA:
        return f"Ollama · {s.model or 'no local model'} (this computer)" + (" · auto" if s.ollama_auto else "")
    return f"Claude · {s.model}"


def public_settings(refresh_ollama: bool = False) -> dict:
    """Settings for the UI. API keys are never returned, only whether each is set in backend/.env."""
    if refresh_ollama:
        ollama_models(refresh=True)
    s = load_settings()
    return {
        "provider": s.provider,
        "model": s.model,
        "label": label(s),
        "claude_model": SCORING_MODEL,
        "claude_key_configured": bool(ANTHROPIC_API_KEY),
        "groq_key_configured": bool(s.groq_api_key),
        "groq_models": [{"id": i, "label": l} for i, l in GROQ_MODELS],
        "groq_limits": GROQ_LIMITS,
        "ollama_model": "" if s.ollama_auto else (s.model if s.provider == OLLAMA else ""),
        "ollama": ollama_overview(),
    }


def not_configured_message(settings: Optional[Settings] = None) -> Optional[str]:
    """None if the active provider can be used, otherwise a message telling the user what to do."""
    s = settings or load_settings()
    if s.provider == GROQ:
        if not s.groq_api_key:
            return "GROQ_API_KEY is not set. Add it to backend/.env and restart the backend."
        return None
    if s.provider == OLLAMA:
        return _ollama_not_ready(s)
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY is not set. Add it to backend/.env and restart the backend."
    return None


STATIC_BUDGET_WINDOW = 8000     # the window (tokens) the Groq/Ollama budget values below were sized for


def input_window_tokens(settings: Optional[Settings] = None) -> int:
    """Tokens one request can hold with the active model: Groq's free tier allows 8,000 per minute whatever the
    model's context window (131,072 for the models offered, as Groq reports); a local Ollama model, the context it
    is run with (at most OLLAMA_NUM_CTX, less if the model's own is smaller); Claude, 200,000."""
    s = settings or load_settings()
    if s.provider == GROQ:
        return GROQ_LIMITS["tpm"]
    if s.provider == OLLAMA:
        try:
            info = _ollama_model_info(s.model) or {}
        except LLMError:
            info = {}
        return min(OLLAMA_NUM_CTX, info.get("context") or OLLAMA_NUM_CTX)
    return 200000


def budget(settings: Optional[Settings] = None) -> dict:
    """Prompt-size caps. Claude values equal the app's original limits; Groq values are
    smaller so a single request stays well inside the 8,000 tokens/minute free-tier cap. A model whose window is
    smaller than the one these were sized for (a small local model) gets proportionally smaller caps, so a
    request still fits and the work is simply done in more parts."""
    s = settings or load_settings()
    if s.provider in (GROQ, OLLAMA):   # both work with small windows (Groq: token cap; Ollama: context size)
        values = {
            "dvm_excerpt_chars": 3000,
            "article_chars": 3000,
            "reference_chars": 8000,
            "chat_context_tables": 25,
            "chat_history_messages": 6,
            "chat_max_rounds": 4,
            "score_chunk": 40,        # candidates per scoring request (sized so the answer fits Qwen's 1,000 output tokens/min)
        }
        scale = min(1.0, input_window_tokens(s) / STATIC_BUDGET_WINDOW)
        if scale < 1.0:
            for key in ("dvm_excerpt_chars", "article_chars", "reference_chars"):
                values[key] = max(600, int(values[key] * scale))
            values["chat_context_tables"] = max(5, int(values["chat_context_tables"] * scale))
            values["chat_history_messages"] = max(2, int(values["chat_history_messages"] * scale))
            values["score_chunk"] = max(5, int(values["score_chunk"] * scale))
        return values
    return {
        "dvm_excerpt_chars": 6000,
        "article_chars": 8000,
        "reference_chars": 14000,
        "chat_context_tables": 60,
        "chat_history_messages": 40,
        "chat_max_rounds": 8,
        "score_chunk": 120,
    }


def groq_output_limit(model: str) -> Optional[int]:
    """The output-tokens-per-minute limit of a Groq model: learned from a refusal, else the known table, else None."""
    return _learned_otpm.get(model) or GROQ_MODEL_LIMITS.get(model, {}).get("otpm")


def max_output_tokens(settings: Optional[Settings] = None) -> int:
    """The most output tokens one answer can have with the active model (a request asking for more would be cut or refused)."""
    s = settings or load_settings()
    if s.provider == GROQ:
        limit = groq_output_limit(s.model)
        return min(GROQ_MAX_OUT, limit) if limit else GROQ_MAX_OUT
    if s.provider == OLLAMA:
        return OLLAMA_MAX_OUT
    return 16000


def max_workers(settings: Optional[Settings] = None) -> int:
    """Parallel requests for batch steps. Groq's per-minute cap makes more than a couple pointless;
    a local model answers one request at a time anyway, so more would only queue up and time out."""
    s = settings or load_settings()
    return {GROQ: 2, OLLAMA: 1}.get(s.provider, 8)


# ---------------------------------------------------------------------------
# Usage meter (per day, per model) -- shown in the UI
# ---------------------------------------------------------------------------

_usage_lock = threading.Lock()


def _record_usage(model: str, tokens: int) -> None:
    today = date.today().isoformat()
    with _usage_lock:
        try:
            data = json.loads(LLM_USAGE_FILE.read_text(encoding="utf-8")) if LLM_USAGE_FILE.exists() else {}
        except Exception:
            data = {}
        if data.get("date") != today:
            data = {"date": today, "models": {}}
        m = data["models"].setdefault(model, {"requests": 0, "tokens": 0})
        m["requests"] += 1
        m["tokens"] += int(tokens or 0)
        try:
            LLM_USAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Could not write %s", LLM_USAGE_FILE)


def usage(settings: Optional[Settings] = None) -> dict:
    s = settings or load_settings()
    today = date.today().isoformat()
    requests = tokens = 0
    with _usage_lock:
        try:
            data = json.loads(LLM_USAGE_FILE.read_text(encoding="utf-8")) if LLM_USAGE_FILE.exists() else {}
        except Exception:
            data = {}
    if data.get("date") == today:
        m = data.get("models", {}).get(s.model, {})
        requests, tokens = m.get("requests", 0), m.get("tokens", 0)
    return {
        "provider": s.provider,
        "model": s.model,
        "date": today,
        "requests": requests,
        "tokens": tokens,
        "limits": GROQ_LIMITS if s.provider == GROQ else None,
    }


# ---------------------------------------------------------------------------
# Groq pacing
# ---------------------------------------------------------------------------

_wait_hook: Optional[Callable[[str], None]] = None
_daily_block: dict[str, str] = {}   # model -> date a daily limit was hit


def set_wait_hook(hook: Optional[Callable[[str], None]]) -> None:
    """Let a running job show 'waiting for the Groq rate limit' in its progress message."""
    global _wait_hook
    _wait_hook = hook


_thread_hook = threading.local()


def set_thread_wait_hook(hook: Optional[Callable[[str], None]]) -> None:
    """Like set_wait_hook, but only for waits that happen on the calling thread (and taking priority over the global
    hook there), so a chat answer can show its own wait without borrowing a running job's progress line."""
    _thread_hook.hook = hook


def _notify(message: str) -> None:
    hook = getattr(_thread_hook, "hook", None) or _wait_hook
    if hook:
        try:
            hook(message)
        except Exception:
            pass


class _RateWindow:
    """Rolling 60-second window of requests, tokens and output tokens; acquire() blocks until a call fits."""

    def __init__(self, rpm: int, tpm: int):
        self._rpm, self._tpm = rpm, tpm
        self._lock = threading.Lock()
        self._events: list[list] = []   # [monotonic time, tokens, output tokens], oldest first

    def acquire(self, tokens: int, out_tokens: int = 0, otpm: Optional[int] = None) -> list:
        cap = int(self._tpm * TPM_HEADROOM)
        announced = False
        while True:
            with self._lock:
                now = time.monotonic()
                self._events = [e for e in self._events if now - e[0] < 60]
                used = sum(e[1] for e in self._events)
                out_used = sum(e[2] for e in self._events)
                fits = (
                    (used + tokens <= cap or used == 0)
                    and (otpm is None or out_used + out_tokens <= otpm or out_used == 0)
                    and len(self._events) < self._rpm
                )
                if fits:
                    event = [now, tokens, out_tokens]
                    self._events.append(event)
                    return event
                wait = max(0.5, 60 - (now - self._events[0][0]) + 0.2)
            if not announced:
                _notify(f"Waiting for the Groq rate limit (~{int(wait)}s)…")
                announced = True
            time.sleep(min(wait, 5))

    def correct(self, event: list, tokens: int, out_tokens: Optional[int] = None) -> None:
        with self._lock:
            event[1] = tokens
            if out_tokens is not None:
                event[2] = out_tokens

    def release(self, event: list) -> None:
        with self._lock:
            if event in self._events:
                self._events.remove(event)


_windows: dict[str, _RateWindow] = {}
_windows_lock = threading.Lock()


def _window(model: str) -> _RateWindow:
    with _windows_lock:
        if model not in _windows:
            _windows[model] = _RateWindow(GROQ_LIMITS["rpm"], GROQ_LIMITS["tpm"])
        return _windows[model]


DEFAULT_CHARS_PER_TOKEN = 3.2       # safe for any text; models that have been measured use their own ratio below
# Measured on Groq (2026-10-14): GPT-OSS 4.8 characters per token on the chat prompt and 4.4 on a list of object names,
# Qwen 3.8 and 4.5. The seeds are lower (an estimate that is too high only costs output allowance; too low risks a refusal).
_CHARS_PER_TOKEN_SEEDS = (("openai/gpt-oss", 4.2), ("qwen/", 3.5))
_calibrated_cpt: dict[str, float] = {}


def chars_per_token(model: str = "") -> float:
    """Characters per token to assume for *model*: learned from its own replies (see _learn_chars_per_token), else
    a measured seed, else the safe default."""
    if model in _calibrated_cpt:
        return _calibrated_cpt[model]
    for prefix, ratio in _CHARS_PER_TOKEN_SEEDS:
        if model.startswith(prefix):
            return ratio
    return DEFAULT_CHARS_PER_TOKEN


def _payload_chars(messages: list, tools: Optional[list] = None) -> int:
    return len(json.dumps(messages, default=str)) + (len(json.dumps(tools)) if tools else 0)


def _learn_chars_per_token(model: str, chars: int, prompt_tokens: int) -> None:
    """Refine the ratio for *model* from a reply that reported how many tokens the prompt really was. The stored ratio
    is 10% below the observed one, so the estimate stays on the high side."""
    if not model or prompt_tokens < 300 or chars < 300:
        return
    observed = min(8.0, max(1.5, chars / prompt_tokens)) * 0.9
    old = _calibrated_cpt.get(model)
    _calibrated_cpt[model] = observed if old is None else 0.7 * old + 0.3 * observed


def _estimate_tokens(messages: list, tools: Optional[list] = None, model: str = "") -> int:
    return int(_payload_chars(messages, tools) / chars_per_token(model)) + 20


def _groq_reason(exc: Exception) -> str:
    """Groq's own explanation of a refusal, shortened for the user (the organisation id is dropped)."""
    message = ""
    try:
        message = exc.body["error"]["message"]  # type: ignore[attr-defined]
    except Exception:
        message = str(exc)
    message = re.sub(r"\s+in organization `[^`]*`( service tier `[^`]*`)?", "", message)
    message = re.sub(r"\s*Need more tokens\?.*$", "", message, flags=re.DOTALL)
    return message.strip()[:260]


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    try:
        header = exc.response.headers.get("retry-after")  # type: ignore[attr-defined]
        if header:
            return float(header)
    except Exception:
        pass
    m = re.search(r"try again in (?:(\d+)m)?\s*([\d.]+)s", str(exc))
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2))
    return None


def _check_daily_block(model: str) -> None:
    if _daily_block.get(model) == date.today().isoformat():
        raise LLMRateLimitError(
            "The Groq daily limit for this model has been reached. Try again after it resets, "
            "pick another Groq model, or switch to Claude in the AI Model panel.",
            daily=True,
        )


# ---------------------------------------------------------------------------
# Groq backend
# ---------------------------------------------------------------------------

_groq_clients: dict[str, Any] = {}


def _groq_client(api_key: str):
    from groq import Groq   # imported lazily so Claude-only installs don't need it

    if api_key not in _groq_clients:
        _groq_clients[api_key] = Groq(api_key=api_key, max_retries=0, timeout=120)
    return _groq_clients[api_key]


def _groq_chat(
    settings: Settings,
    messages: list,
    *,
    max_tokens: int,
    response_format: Optional[dict] = None,
    tools: Optional[list] = None,
    on_token: Optional[Callable[[str], None]] = None,
):
    """One Groq request. With *on_token* the answer is streamed: each piece of text is passed to it as it arrives,
    and the same response object is returned as without streaming (the pieces put back together)."""
    import groq

    model = settings.model
    _check_daily_block(model)

    est_in = _estimate_tokens(messages, tools, model)
    tpm = GROQ_LIMITS["tpm"]
    if est_in + 200 > tpm:
        raise LLMTooLargeError(
            f"This request is about {est_in:,} tokens, more than Groq's free tier allows in one "
            f"request ({tpm:,} tokens/minute). Use a smaller input or switch to Claude."
        )
    kwargs: dict[str, Any] = dict(model=model, messages=messages, temperature=0)
    if model.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = "low"   # reasoning tokens count against the same limits
    elif model.startswith("qwen/") and model not in _reasoning_unsupported:
        kwargs["reasoning_effort"] = "none"  # thinking tokens would use up the small output allowance
    if response_format:
        kwargs["response_format"] = response_format
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if on_token:
        kwargs["stream"] = True      # Groq reports the usage in the last chunk by itself (x_groq.usage)

    client = _groq_client(settings.groq_api_key)
    window = _window(model)

    last_reason = ""
    for attempt in range(MAX_RATE_RETRIES):
        # asked for afresh each time: a refusal can teach the app the model's output limit
        otpm = groq_output_limit(model)
        out_cap = max(200, min(max_tokens, GROQ_MAX_OUT, int(tpm * 0.95) - est_in))
        if otpm:
            out_cap = min(out_cap, otpm)
        reserve = est_in + min(out_cap, 800)
        kwargs["max_completion_tokens"] = out_cap
        event = window.acquire(reserve, out_cap, otpm)
        try:
            response = client.chat.completions.create(**kwargs)
        except groq.RateLimitError as exc:
            window.release(event)
            text = str(exc).lower()
            last_reason = _groq_reason(exc)
            if "per day" in text or "tpd" in text or "rpd" in text:
                _daily_block[model] = date.today().isoformat()
                raise LLMRateLimitError(
                    "The Groq daily limit for this model has been reached "
                    f"({GROQ_LIMITS['rpd']:,} requests / {GROQ_LIMITS['tpd']:,} tokens per day). "
                    "Try again after it resets, pick another Groq model, or switch to Claude "
                    "in the AI Model panel.",
                    daily=True,
                ) from exc
            too_large = re.search(r"request too large.*?\((o?tpm)\):\s*limit\s*(\d+)", text, re.DOTALL)
            if too_large:
                # Resending the same request can never work: say what is wrong, or ask for less and go again
                kind, limit = too_large.group(1), int(too_large.group(2))
                if kind != "otpm":
                    raise LLMTooLargeError(f"Groq refuses a request this large on the free tier: {last_reason}") from exc
                if out_cap <= limit and _learned_otpm.get(model) == limit:
                    raise LLMTooLargeError(f"Groq refuses this many output tokens: {last_reason}") from exc
                _learned_otpm[model] = limit
                _notify(f"Groq allows {limit:,} output tokens per minute for this model; asking for less…")
                continue
            wait = _retry_after_seconds(exc) or 10 * (attempt + 1)
            _notify(f"Groq rate limit reached; retrying in ~{int(wait)}s…")
            time.sleep(min(wait + 0.5, 90))
            continue
        except groq.AuthenticationError as exc:
            window.release(event)
            raise LLMAuthError("Groq rejected the API key. Check GROQ_API_KEY in backend/.env.") from exc
        except groq.BadRequestError as exc:
            window.release(event)
            if "reasoning_effort" in kwargs and model.startswith("qwen/") and "reasoning" in str(exc).lower():
                kwargs.pop("reasoning_effort")           # this model does not take it: go on without
                _reasoning_unsupported.add(model)
                continue
            raise LLMBadRequestError(f"Groq rejected the request: {exc}") from exc
        except groq.APIStatusError as exc:
            window.release(event)
            if exc.status_code == 413:
                raise LLMTooLargeError(
                    "Groq says the request is too large for the free tier. "
                    "Use a smaller input or switch to Claude."
                ) from exc
            raise LLMError(f"Groq error {exc.status_code}: {exc}") from exc
        except groq.APIConnectionError as exc:
            window.release(event)
            raise LLMError("Could not reach Groq. Check the internet connection.") from exc

        if on_token:
            # Refusals (rate limits, bad requests) arrive before any text, so the retry loop above never resends a
            # request whose text the user has already seen; a failure part-way through is raised, not retried.
            try:
                response = _groq_collect(response, on_token)
            except BaseException:
                window.release(event)
                raise

        usage = getattr(response, "usage", None)
        total = getattr(usage, "total_tokens", None) or reserve
        actual_out = getattr(usage, "completion_tokens", None)
        _learn_chars_per_token(model, _payload_chars(messages, tools), int(getattr(usage, "prompt_tokens", 0) or 0))
        window.correct(event, int(total), None if OTPM_COUNTS_REQUESTED or actual_out is None else int(actual_out))
        _record_usage(model, int(total))
        return response

    raise LLMRateLimitError(
        "Groq kept refusing the request" + (f" ({last_reason})" if last_reason else "")
        + ". Wait a minute and try again, or switch to Claude.",
        daily=False,
    )


def _groq_collect(stream, on_token: Callable[[str], None]):
    """Read a streamed Groq answer to its end, passing each piece of text to *on_token*, and return an object shaped
    like the non-streamed response (choices[0].message.content / .tool_calls, usage)."""
    import groq

    content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage = None
    finish = None
    try:
        for chunk in stream:
            usage = getattr(chunk, "usage", None) or getattr(getattr(chunk, "x_groq", None), "usage", None) or usage
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            piece = getattr(delta, "content", None)
            if piece:
                content.append(piece)
                on_token(piece)
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = calls.setdefault(tc.index or 0, {"id": "", "name": "", "arguments": ""})
                slot["id"] = tc.id or slot["id"]
                fn = getattr(tc, "function", None)
                if fn is not None:
                    slot["name"] = fn.name or slot["name"]
                    slot["arguments"] += fn.arguments or ""
            finish = choice.finish_reason or finish
    except groq.APIError as exc:
        raise LLMError(f"Groq stopped answering part-way ({exc}). Please try again.") from exc

    tool_calls = [
        types.SimpleNamespace(id=c["id"], function=types.SimpleNamespace(name=c["name"], arguments=c["arguments"]))
        for _, c in sorted(calls.items())
    ]
    message = types.SimpleNamespace(content="".join(content), tool_calls=tool_calls or None)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message, finish_reason=finish)], usage=usage)


def _join_rounds(earlier: list[str], last: str) -> str:
    """The answer of a streamed chat turn: the text the model wrote before each tool call, then its final text,
    separated by blank lines -- exactly what was streamed to the user."""
    return "\n\n".join(p for p in [*earlier, last] if p)


CUT_OFF_MESSAGE = (
    "The model's answer was cut off before it finished (its output limit was reached). "
    "Try fewer items at a time or switch to Claude."
)
CUT_OFF_NOTE = "[The answer was cut off by the model's length limit.]"
CONTINUE_PROMPT = "Continue exactly where you stopped. Do not repeat anything you already wrote."
MAX_CONTINUATIONS = 2       # how many times a chat answer that hit the length limit is asked to go on


class _Answer:
    """The text of one chat answer for the three tool loops: what the model wrote before each tool call, then the
    final text -- which takes several requests when the model hits its length limit (it is asked to go on, up to
    MAX_CONTINUATIONS times, and the answer says so if it is still cut off)."""

    def __init__(self, on_token: Optional[Callable[[str], None]]):
        self.on_token = on_token
        self.earlier: list[str] = []    # text before tool calls (only kept when streaming, as before)
        self.parts: list[str] = []      # the final text so far, one piece per request
        self.continuations = 0

    def tool_round(self, written: str) -> None:
        """A request ended in tool calls: any text before them stays in the answer."""
        text = ("".join(self.parts) + written).strip()
        self.parts = []
        if self.on_token and text:
            self.earlier.append(text)
            self.on_token("\n\n")

    def text_round(self, written: str, cut: bool) -> Optional[str]:
        """A request ended in text. Returns the finished answer, or None when the model has to be asked to go on."""
        self.parts.append(written)
        if cut and self.continuations < MAX_CONTINUATIONS:
            self.continuations += 1
            return None
        final = "".join(self.parts).strip()
        if cut:
            logger.warning("A chat answer was still cut off after %d continuation(s)", self.continuations)
            final = f"{final}\n\n{CUT_OFF_NOTE}"
            if self.on_token:
                self.on_token(f"\n\n{CUT_OFF_NOTE}")
        return _join_rounds(self.earlier, final)


_DROP_KEYS = {
    "title", "default", "examples", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "format",
}


def _strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic schema -> the closed, all-required, $ref-free form Groq's strict mode wants.
    Range checks (e.g. score 0-100) are dropped from the schema and enforced by Pydantic
    when the reply is validated."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _DROP_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: walk(prop) for name, prop in value.items()}
            else:
                out[key] = walk(value)
        if out.get("type") == "object" or "properties" in out:
            props = out.setdefault("properties", {})
            out["required"] = list(props)
            out["additionalProperties"] = False
        return out

    return walk(schema)


def _parse_json(content: str) -> Any:
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[4:].strip() if raw.lower().startswith("json") else raw.strip()
    return json.loads(raw)


def _groq_structured(
    settings: Settings, system: str, user: str, schema: type[BaseModel], max_tokens: int
) -> BaseModel:
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    json_schema = _strict_schema(schema)
    response_format: dict = {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "strict": True, "schema": json_schema},
    }
    json_mode = False

    for attempt in range(2):
        try:
            response = _groq_chat(
                settings, messages, max_tokens=max_tokens, response_format=response_format
            )
        except LLMBadRequestError:
            if json_mode:
                raise
            # The model/key refused strict JSON-schema output: fall back to plain JSON mode with
            # the schema spelled out in the prompt (still validated with Pydantic below).
            json_mode = True
            response_format = {"type": "json_object"}
            messages[0] = {
                "role": "system",
                "content": system + "\n\nReply with ONLY a JSON object matching this JSON Schema:\n"
                + json.dumps(json_schema),
            }
            response = _groq_chat(
                settings, messages, max_tokens=max_tokens, response_format=response_format
            )

        choice = response.choices[0]
        content = choice.message.content or ""
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMCutOffError(CUT_OFF_MESSAGE, partial=content)
        try:
            return schema.model_validate(_parse_json(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == 1:
                raise LLMError(
                    f"The model returned an answer in the wrong format: {str(exc).splitlines()[0][:200]}"
                ) from exc
            messages = messages + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "That did not match the required JSON format "
                    f"({str(exc).splitlines()[0][:200]}). Reply again with ONLY the corrected JSON.",
                },
            ]
    raise LLMError("The model did not return a usable answer.")


def _groq_text(settings: Settings, system: str, user: str, max_tokens: int) -> str:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    response = _groq_chat(settings, messages, max_tokens=max_tokens)
    choice = response.choices[0]
    content = (choice.message.content or "").strip()
    if getattr(choice, "finish_reason", None) == "length":
        raise LLMCutOffError(CUT_OFF_MESSAGE, partial=content)
    return content


def _groq_tool_loop(
    settings: Settings,
    system: str,
    history: list[dict],
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_rounds: int,
    on_token: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    oa_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]
    messages: list[dict] = [{"role": "system", "content": system}] + list(history)
    answer = _Answer(on_token)
    rounds, continuing = 0, False

    while True:
        if not continuing:
            if rounds >= max_rounds:
                return None
            rounds += 1
        continuing = False
        response = _groq_chat(settings, messages, max_tokens=1500, tools=oa_tools, on_token=on_token)
        choice = response.choices[0]
        message = choice.message
        tool_calls = getattr(message, "tool_calls", None) or []
        written = message.content or ""
        cut = getattr(choice, "finish_reason", None) == "length"
        if not tool_calls:
            final = answer.text_round(written, cut)
            if final is not None:
                return final
            messages += [{"role": "assistant", "content": written}, {"role": "user", "content": CONTINUE_PROMPT}]
            continuing = True       # asking the model to go on is not a tool round
            continue
        if cut:
            raise LLMCutOffError("The model's request to look something up was cut off by its length limit.", partial=written)
        answer.tool_round(written)

        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                }
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            logger.info("Chat tool call: %s(%s)", tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": execute_tool(tc.function.name, args),
            })


# ---------------------------------------------------------------------------
# Ollama backend (runs on this computer)
# ---------------------------------------------------------------------------

_ollama_lock = threading.Lock()
_ollama_cache: dict[str, Any] = {"at": 0.0, "result": None}
_ollama_caps: dict[str, Optional[list]] = {}   # model digest -> capabilities (None = server doesn't say)


def _ollama_base() -> str:
    """The Ollama address, only if it is this computer. Anything else is refused, so a wrong OLLAMA_URL can
    never send prompts (table names, SAP content) to another machine."""
    url = (OLLAMA_URL or "").strip().rstrip("/") or "http://127.0.0.1:11434"
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower()
    local = host == "localhost"
    if not local:
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
    if not local:
        raise LLMNotConfiguredError(
            f"OLLAMA_URL points to '{host}', which is not this computer. For privacy the app only uses Ollama on "
            "localhost. Set OLLAMA_URL=http://127.0.0.1:11434 in backend/.env (or remove it)."
        )
    return url


def _read_ollama_stream(response, on_piece: Callable[[str], None]) -> dict:
    """Read Ollama's streamed answer (one JSON object per line), passing each piece of text to *on_piece*, and return
    one dict shaped like the non-streamed answer."""
    content: list[str] = []
    calls: list = []
    last: dict = {}
    for line in response.iter_lines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("error"):
            raise LLMError(f"Ollama error: {str(obj['error'])[:300]}")
        message = obj.get("message") or {}
        piece = message.get("content") or ""
        if piece:
            content.append(piece)
            on_piece(piece)
        calls.extend(message.get("tool_calls") or [])
        if obj.get("done"):
            last = obj
    merged = dict(last)
    merged["message"] = {"role": "assistant", "content": "".join(content), "tool_calls": calls or None}
    return merged


def _hide_think(on_token: Callable[[str], None]):
    """Wrap *on_token* so text inside <think>...</think> is not passed on (the non-streamed path strips it too).
    Returns (feed, flush); call flush() when the answer is complete."""
    OPEN, CLOSE = "<think>", "</think>"
    state = {"buf": "", "hidden": False}

    def feed(piece: str) -> None:
        state["buf"] += piece
        out: list[str] = []
        while True:
            buf = state["buf"]
            if state["hidden"]:
                end = buf.find(CLOSE)
                if end < 0:
                    state["buf"] = buf[-(len(CLOSE) - 1):]      # a closing tag may be split across pieces
                    break
                state["buf"], state["hidden"] = buf[end + len(CLOSE):], False
                continue
            start = buf.find(OPEN)
            if start >= 0:
                out.append(buf[:start])
                state["buf"], state["hidden"] = buf[start + len(OPEN):], True
                continue
            keep = next((n for n in range(min(len(OPEN) - 1, len(buf)), 0, -1) if OPEN.startswith(buf[-n:])), 0)
            out.append(buf[:len(buf) - keep])
            state["buf"] = buf[len(buf) - keep:]
            break
        text = "".join(out)
        if text:
            on_token(text)

    def flush() -> None:
        if not state["hidden"] and state["buf"]:
            on_token(state["buf"])
        state["buf"] = ""

    return feed, flush


def _ollama_request(
    method: str, path: str, payload: Optional[dict] = None, timeout: float = 10.0,
    on_piece: Optional[Callable[[str], None]] = None,
):
    base = _ollama_base()
    streamed: Optional[dict] = None
    try:
        # trust_env=False: never send local traffic through a corporate proxy
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0), trust_env=False) as client:
            if on_piece is None:
                response = client.request(method, base + path, json=payload)
            else:
                with client.stream(method, base + path, json=payload) as response:
                    if response.status_code < 400:
                        streamed = _read_ollama_stream(response, on_piece)
                    else:
                        response.read()
    except httpx.ConnectError as exc:
        raise LLMError(
            f"Could not reach Ollama at {base}. Start the Ollama app (or run 'ollama serve') and try again."
        ) from exc
    except httpx.TimeoutException as exc:
        raise LLMError(
            f"Ollama did not answer within {int(timeout)}s. The model is probably too big for this computer "
            "(or still loading) - pick a smaller model in the AI Model panel."
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"Ollama request failed: {exc}") from exc
    if streamed is not None:
        return streamed
    if response.status_code >= 400:
        try:
            detail = str(response.json().get("error") or response.text)
        except Exception:
            detail = response.text
        detail = detail.strip()[:300]
        if response.status_code == 404 and "not found" in detail.lower():
            name = (payload or {}).get("model", "the model")
            raise LLMError(f"Ollama does not have '{name}' installed. Run: ollama pull {name}")
        if response.status_code == 400:
            raise LLMBadRequestError(f"Ollama rejected the request: {detail}")
        raise LLMError(f"Ollama error {response.status_code}: {detail}")
    return response.json()


def _is_cloud_name(name: str) -> bool:
    n = (name or "").lower()
    return n.endswith(":cloud") or n.endswith("-cloud")


def _total_ram_bytes() -> Optional[int]:
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemStatus()
        status.dwLength = ctypes.sizeof(status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        return int(status.ullTotalPhys) or None
    except Exception:
        return None


def _billions(text: str) -> float:
    m = re.match(r"\s*([\d.]+)\s*([KMBT]?)", str(text or ""), re.IGNORECASE)
    if not m:
        return 0.0
    return float(m.group(1)) * {"": 1e-9, "K": 1e-6, "M": 1e-3, "B": 1.0, "T": 1e3}[m.group(2).upper()]


def _capabilities(entry: dict) -> Optional[list]:
    caps = entry.get("capabilities")
    if isinstance(caps, list):
        return [str(c) for c in caps]
    digest = entry.get("digest", entry.get("name"))
    if digest not in _ollama_caps:
        try:
            shown = _ollama_request("POST", "/api/show", {"model": entry["name"]}, timeout=10)
            c = shown.get("capabilities")
            _ollama_caps[digest] = [str(x) for x in c] if isinstance(c, list) else None
        except LLMError:
            _ollama_caps[digest] = None
    return _ollama_caps[digest]


def _rank_key(m: dict) -> tuple:
    # fits in memory > can use tools (the chat needs them) > big enough to be useful > more parameters > newer
    return (m["fits"], m["tools"], not m["too_small"], m["params_b"], m["modified"])


def ollama_models(refresh: bool = False) -> dict:
    """The models installed on this computer: {"reachable", "error", "models": [...], "hidden_cloud": n,
    "ram_gb"}. Cached for a few seconds. Models Ollama would run in its cloud are left out.
    Raises LLMNotConfiguredError if OLLAMA_URL is not this computer (checked even for a cached answer)."""
    _ollama_base()
    with _ollama_lock:
        cached = _ollama_cache["result"]
        if cached is not None and not refresh and time.monotonic() - _ollama_cache["at"] < OLLAMA_CACHE_SECONDS:
            return cached
    result: dict = {"reachable": False, "error": "", "models": [], "hidden_cloud": 0, "ram_gb": None}
    ram = _total_ram_bytes()
    if ram:
        result["ram_gb"] = round(ram / 1024 ** 3, 1)
    try:
        tags = _ollama_request("GET", "/api/tags", timeout=10)
        result["reachable"] = True
        for entry in tags.get("models", []):
            name = entry.get("name") or entry.get("model") or ""
            if not name:
                continue
            if _is_cloud_name(name) or entry.get("remote_host") or entry.get("remote_model"):
                result["hidden_cloud"] += 1
                continue
            caps = _capabilities({**entry, "name": name})
            details = entry.get("details") or {}
            size = int(entry.get("size") or 0)
            params_b = _billions(details.get("parameter_size", ""))
            usable = caps is None or "completion" in caps
            if not usable:   # embedding-only models cannot answer questions
                continue
            result["models"].append({
                "id": name,
                "size_gb": round(size / 1024 ** 3, 1),
                "params": details.get("parameter_size", ""),
                "params_b": params_b,
                "quantization": details.get("quantization_level", ""),
                "context": int(details.get("context_length") or 0) or None,
                "tools": bool(caps and "tools" in caps),
                "thinking": bool(caps and "thinking" in caps),
                "fits": not ram or not size or size <= ram * OLLAMA_RAM_SHARE,
                "too_small": 0 < params_b < OLLAMA_MIN_USEFUL_B,
                "modified": str(entry.get("modified_at") or ""),
            })
        result["models"].sort(key=_rank_key, reverse=True)
    except LLMError as exc:
        result["error"] = str(exc)
    with _ollama_lock:
        _ollama_cache.update(at=time.monotonic(), result=result)
    return result


def _best_local_model() -> str:
    """The model "Auto" uses: the highest ranked installed one ('' if there is none or Ollama is down)."""
    try:
        models = ollama_models()["models"]
    except LLMError:
        return ""
    return models[0]["id"] if models else ""


def _ollama_model_info(model: str) -> Optional[dict]:
    return next((m for m in ollama_models()["models"] if m["id"] == model), None)


def _ollama_not_ready(s: Settings) -> Optional[str]:
    try:
        info = ollama_models()
    except LLMError as exc:   # e.g. OLLAMA_URL is not local
        return str(exc)
    if not info["reachable"]:
        return info["error"]
    if not s.model:
        return "No local model is installed in Ollama. Run: ollama pull " + _suggested_pulls(info)[0]
    if _is_cloud_name(s.model):
        return f"'{s.model}' runs in Ollama's cloud, not on this computer. Pick a local model."
    if s.model not in [m["id"] for m in info["models"]]:
        return f"'{s.model}' is not installed in Ollama. Run: ollama pull {s.model}"
    return None


def _suggested_pulls(info: dict) -> list[str]:
    ram = info.get("ram_gb")
    fit = [name for name, gb in OLLAMA_SUGGESTED if not ram or gb <= ram * OLLAMA_RAM_SHARE]
    return fit[:2] or [OLLAMA_SUGGESTED[-1][0]]


def ollama_overview() -> dict:
    """What the AI Model panel shows for Ollama: the local models, the automatic pick, and advice."""
    try:
        info = ollama_models()
    except LLMError as exc:
        info = {"reachable": False, "error": str(exc), "models": [], "hidden_cloud": 0, "ram_gb": None}
    models = info["models"]
    best = models[0] if models else None
    notice = ""
    if not info["reachable"]:
        notice = info["error"]
    elif not models:
        notice = "No local model is installed yet. Run: ollama pull " + _suggested_pulls(info)[0]
    elif best["too_small"] or not best["tools"]:
        why = "too small to give reliable results" if best["too_small"] else "unable to use tools (the chat needs them)"
        notice = (f"The best installed model ({best['id']}) is {why}. "
                  "For better answers run: ollama pull " + _suggested_pulls(info)[0])
    return {
        "url": _safe_url(),
        "reachable": info["reachable"],
        "error": info["error"],
        "notice": notice,
        "models": [
            {k: m[k] for k in ("id", "size_gb", "params", "tools", "fits", "too_small")} | {"best": m is best}
            for m in models
        ],
        "auto_model": best["id"] if best else "",
        "hidden_cloud": info["hidden_cloud"],
        "ram_gb": info["ram_gb"],
        "suggested": _suggested_pulls(info),
    }


def _safe_url() -> str:
    try:
        return _ollama_base()
    except LLMNotConfiguredError:
        return OLLAMA_URL


_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)


def _ollama_chat(
    settings: Settings,
    messages: list,
    *,
    max_tokens: int,
    schema: Optional[dict] = None,
    tools: Optional[list] = None,
    on_piece: Optional[Callable[[str], None]] = None,
) -> dict:
    model = settings.model
    info = _ollama_model_info(model) or {}
    ctx = min(OLLAMA_NUM_CTX, info.get("context") or OLLAMA_NUM_CTX)
    est_in = _estimate_tokens(messages, tools, model)
    if est_in + 300 > ctx:
        raise LLMTooLargeError(
            f"This request is about {est_in:,} tokens, more than the {ctx:,}-token window used for the local model. "
            "Use a smaller input or switch to Claude."
        )
    out_cap = max(200, min(max_tokens, OLLAMA_MAX_OUT, ctx - est_in - 100))
    payload: dict[str, Any] = {
        "model": model, "messages": messages, "stream": False, "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": ctx, "num_predict": out_cap},
    }
    if schema:
        payload["format"] = schema
    if tools:
        payload["tools"] = tools
    if info.get("thinking"):   # thinking tokens are slow on a local CPU; gpt-oss can only be turned down
        payload["think"] = "low" if model.lower().startswith("gpt-oss") else False

    if on_piece:
        payload["stream"] = True
    data = _ollama_request("POST", "/api/chat", payload, timeout=OLLAMA_TIMEOUT, on_piece=on_piece)
    _record_usage(model, int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0))
    _learn_chars_per_token(model, _payload_chars(messages, tools), int(data.get("prompt_eval_count") or 0))
    return data


def _ollama_content(data: dict) -> str:
    return _THINK_TAGS.sub("", (data.get("message") or {}).get("content") or "").strip()


def _ollama_structured(settings: Settings, system: str, user: str, schema: type[BaseModel], max_tokens: int) -> BaseModel:
    json_schema = _strict_schema(schema)
    messages: list[dict] = [
        {"role": "system", "content": system + "\n\nReply with ONLY a JSON object matching this JSON Schema:\n"
         + json.dumps(json_schema)},
        {"role": "user", "content": user},
    ]
    for attempt in range(2):
        data = _ollama_chat(settings, messages, max_tokens=max_tokens, schema=json_schema)
        content = _ollama_content(data)
        if data.get("done_reason") == "length":
            raise LLMCutOffError(
                "The local model's answer was cut off before it finished (output limit reached). "
                "Try fewer items at a time, pick a larger model, or switch to Claude.",
                partial=content,
            )
        try:
            return schema.model_validate(_parse_json(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == 1:
                raise LLMError(
                    f"The local model returned an answer in the wrong format: {str(exc).splitlines()[0][:200]}. "
                    "Small models do this often - pick a larger one or switch to Claude."
                ) from exc
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "That did not match the required JSON format "
                 f"({str(exc).splitlines()[0][:200]}). Reply again with ONLY the corrected JSON."},
            ]
    raise LLMError("The model did not return a usable answer.")


def _ollama_text(settings: Settings, system: str, user: str, max_tokens: int) -> str:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    data = _ollama_chat(settings, messages, max_tokens=max_tokens)
    content = _ollama_content(data)
    if data.get("done_reason") == "length":
        raise LLMCutOffError(CUT_OFF_MESSAGE, partial=content)
    return content


def _ollama_tool_loop(
    settings: Settings,
    system: str,
    history: list[dict],
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_rounds: int,
    on_token: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    info = _ollama_model_info(settings.model)
    if info is not None and not info["tools"]:
        raise LLMError(
            f"'{settings.model}' cannot call tools, which the chat assistant needs. Pick a local model that supports "
            f"tools (for example: ollama pull {_suggested_pulls(ollama_models())[0]}) or switch to Claude."
        )
    oa_tools = [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools
    ]
    messages: list[dict] = [{"role": "system", "content": system}] + list(history)

    answer = _Answer(on_token)
    rounds, continuing = 0, False

    while True:
        if not continuing:
            if rounds >= max_rounds:
                return None
            rounds += 1
        continuing = False
        feed, flush = _hide_think(on_token) if on_token else (None, None)
        data = _ollama_chat(settings, messages, max_tokens=1500, tools=oa_tools, on_piece=feed)
        if flush:
            flush()
        message = data.get("message") or {}
        calls = message.get("tool_calls") or []
        written = _THINK_TAGS.sub("", message.get("content") or "")
        cut = data.get("done_reason") == "length"
        if not calls:
            final = answer.text_round(written, cut)
            if final is not None:
                return final
            messages += [{"role": "assistant", "content": written}, {"role": "user", "content": CONTINUE_PROMPT}]
            continuing = True       # asking the model to go on is not a tool round
            continue
        if cut:
            raise LLMCutOffError("The model's request to look something up was cut off by its length limit.", partial=written)
        answer.tool_round(written)

        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            logger.info("Chat tool call: %s(%s)", name, args)
            messages.append({"role": "tool", "tool_name": name, "content": execute_tool(name, args)})


# ---------------------------------------------------------------------------
# Anthropic backend (behaviour identical to the pre-llm.py code)
# ---------------------------------------------------------------------------

_anthropic_clients: dict[str, anthropic.Anthropic] = {}


def _anthropic_client() -> anthropic.Anthropic:
    if ANTHROPIC_API_KEY not in _anthropic_clients:
        _anthropic_clients[ANTHROPIC_API_KEY] = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_clients[ANTHROPIC_API_KEY]


def _anthropic_errors(fn):
    """Translate Anthropic SDK exceptions into the provider-neutral ones."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in backend/.env.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(f"Anthropic rate limit reached: {exc}", daily=False) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach Anthropic: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic error: {exc}") from exc

    return wrapper


def _usage_of(response) -> int:
    u = getattr(response, "usage", None)
    return int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "output_tokens", 0) or 0)


@_anthropic_errors
def _anthropic_structured(settings: Settings, system: str, user: str, schema, max_tokens: int):
    try:
        response = _anthropic_client().messages.parse(
            model=settings.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
    except ValidationError as exc:
        # The SDK reads the answer text itself and raises before the stop reason can be looked at; JSON that ends
        # abruptly is what an answer cut off by the output limit looks like.
        if any(e.get("type") == "json_invalid" for e in exc.errors()):
            raise LLMCutOffError(CUT_OFF_MESSAGE) from exc
        raise LLMError(f"The model returned an answer in the wrong format: {str(exc).splitlines()[0][:200]}") from exc
    _record_usage(settings.model, _usage_of(response))
    return response.parsed_output


@_anthropic_errors
def _anthropic_text(settings: Settings, system: str, user: str, max_tokens: int) -> str:
    kwargs: dict[str, Any] = dict(
        model=settings.model, max_tokens=max_tokens, messages=[{"role": "user", "content": user}]
    )
    if system:
        kwargs["system"] = system
    response = _anthropic_client().messages.create(**kwargs)
    _record_usage(settings.model, _usage_of(response))
    content = response.content[0].text.strip()
    if response.stop_reason == "max_tokens":
        raise LLMCutOffError(CUT_OFF_MESSAGE, partial=content)
    return content


@_anthropic_errors
def _anthropic_tool_loop(
    settings: Settings,
    system: str,
    history: list[dict],
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_rounds: int,
    on_token: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    client = _anthropic_client()
    messages: list[dict[str, Any]] = list(history)
    answer = _Answer(on_token)
    rounds, continuing = 0, False

    while True:
        if not continuing:
            if rounds >= max_rounds:
                return None
            rounds += 1
        continuing = False
        if on_token:
            with client.messages.stream(
                model=settings.model, max_tokens=1500, system=system, messages=messages, tools=tools
            ) as stream:
                for piece in stream.text_stream:
                    on_token(piece)
                response = stream.get_final_message()
        else:
            response = client.messages.create(
                model=settings.model, max_tokens=1500, system=system, messages=messages, tools=tools
            )
        _record_usage(settings.model, _usage_of(response))

        written = "".join(b.text for b in response.content if hasattr(b, "text"))
        if response.stop_reason != "tool_use":
            cut = response.stop_reason == "max_tokens"
            if cut and not written.strip():
                raise LLMCutOffError(CUT_OFF_MESSAGE)
            final = answer.text_round(written, cut)
            if final is not None:
                return final
            messages += [{"role": "assistant", "content": written}, {"role": "user", "content": CONTINUE_PROMPT}]
            continuing = True       # asking the model to go on is not a tool round
            continue
        answer.tool_round(written)

        assistant_content = list(response.content)
        tool_results = []
        for block in assistant_content:
            if block.type != "tool_use":
                continue
            logger.info("Chat tool call: %s(%s)", block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": execute_tool(block.name, block.input),
            })
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _ready(settings: Optional[Settings]) -> Settings:
    s = settings or load_settings()
    problem = not_configured_message(s)
    if problem:
        raise LLMNotConfiguredError(problem)
    return s


def structured(
    system: str, user: str, schema: type[BaseModel], max_tokens: int = 1024,
    settings: Optional[Settings] = None,
) -> BaseModel:
    """One call returning a validated *schema* instance."""
    s = _ready(settings)
    if s.provider == OLLAMA:
        return _ollama_structured(s, system, user, schema, max_tokens)
    if s.provider == GROQ:
        return _groq_structured(s, system, user, schema, max_tokens)
    return _anthropic_structured(s, system, user, schema, max_tokens)


def text(
    system: str, user: str, max_tokens: int = 1024, settings: Optional[Settings] = None
) -> str:
    """One call returning plain text."""
    s = _ready(settings)
    if s.provider == OLLAMA:
        return _ollama_text(s, system, user, max_tokens)
    if s.provider == GROQ:
        return _groq_text(s, system, user, max_tokens)
    return _anthropic_text(s, system, user, max_tokens)


def run_tool_loop(
    system: str,
    messages: list[dict],
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_rounds: int = 8,
    settings: Optional[Settings] = None,
    on_token: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Let the model call *tools* (Anthropic-format schemas) until it answers.
    *execute_tool(name, input)* runs a tool and returns its result as text. Returns the final
    reply, or None if *max_rounds* tool rounds were used without a final answer.

    With *on_token* the answer is streamed: every piece of text the model writes is passed to it as it arrives
    (text written before a tool call is followed by a blank line), and the reply returned is that same text."""
    s = _ready(settings)
    if s.provider == OLLAMA:
        return _ollama_tool_loop(s, system, messages, tools, execute_tool, max_rounds, on_token)
    if s.provider == GROQ:
        return _groq_tool_loop(s, system, messages, tools, execute_tool, max_rounds, on_token)
    return _anthropic_tool_loop(s, system, messages, tools, execute_tool, max_rounds, on_token)


def test_connection(settings: Optional[Settings] = None) -> dict:
    """One tiny call to check the saved key/model work. Never raises."""
    s = settings or load_settings()
    started = time.monotonic()
    try:
        reply = text("", "Reply with the single word OK.", max_tokens=20, settings=s)
    except LLMError as exc:
        return {"ok": False, "label": label(s), "message": str(exc)}
    except Exception as exc:  # unexpected SDK/network failure: still report, don't crash the endpoint
        logger.warning("LLM test failed: %s", exc, exc_info=True)
        return {"ok": False, "label": label(s), "message": f"Unexpected error: {exc}"}
    return {
        "ok": True,
        "label": label(s),
        "reply": reply[:80],
        "seconds": round(time.monotonic() - started, 1),
    }
