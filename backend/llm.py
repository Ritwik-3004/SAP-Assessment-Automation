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
    ("qwen/qwen3.8-27b", "Qwen 3.8 27B"),
)
GROQ_MODEL_IDS = tuple(m[0] for m in GROQ_MODELS)

# Free-tier limits (per model, per organisation) from console.groq.com/docs/rate-limits.
GROQ_LIMITS = {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 200000}
TPM_HEADROOM = 0.85       # plan to use at most this share of the per-minute token budget
GROQ_MAX_OUT = 4000       # cap on completion tokens requested from Groq
MAX_RATE_RETRIES = 6

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


def budget(settings: Optional[Settings] = None) -> dict:
    """Prompt-size caps. Claude values equal the app's original limits; Groq values are
    smaller so a single request stays well inside the 8,000 tokens/minute free-tier cap."""
    s = settings or load_settings()
    if s.provider in (GROQ, OLLAMA):   # both work with small windows (Groq: token cap; Ollama: context size)
        return {
            "dvm_excerpt_chars": 3000,
            "article_chars": 3000,
            "reference_chars": 8000,
            "chat_context_tables": 25,
            "chat_history_messages": 6,
            "chat_max_rounds": 4,
        }
    return {
        "dvm_excerpt_chars": 6000,
        "article_chars": 8000,
        "reference_chars": 14000,
        "chat_context_tables": 60,
        "chat_history_messages": 40,
        "chat_max_rounds": 8,
    }


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


def _notify(message: str) -> None:
    hook = _wait_hook
    if hook:
        try:
            hook(message)
        except Exception:
            pass


class _RateWindow:
    """Rolling 60-second window of requests/tokens; acquire() blocks until a call fits."""

    def __init__(self, rpm: int, tpm: int):
        self._rpm, self._tpm = rpm, tpm
        self._lock = threading.Lock()
        self._events: list[list] = []   # [monotonic time, tokens], oldest first

    def acquire(self, tokens: int) -> list:
        cap = int(self._tpm * TPM_HEADROOM)
        announced = False
        while True:
            with self._lock:
                now = time.monotonic()
                self._events = [e for e in self._events if now - e[0] < 60]
                used = sum(e[1] for e in self._events)
                fits = (used + tokens <= cap or used == 0) and len(self._events) < self._rpm
                if fits:
                    event = [now, tokens]
                    self._events.append(event)
                    return event
                wait = max(0.5, 60 - (now - self._events[0][0]) + 0.2)
            if not announced:
                _notify(f"Waiting for the Groq rate limit (~{int(wait)}s)…")
                announced = True
            time.sleep(min(wait, 5))

    def correct(self, event: list, tokens: int) -> None:
        with self._lock:
            event[1] = tokens

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


def _estimate_tokens(messages: list, tools: Optional[list] = None) -> int:
    chars = len(json.dumps(messages, default=str)) + (len(json.dumps(tools)) if tools else 0)
    return int(chars / 3.2) + 20


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
):
    import groq

    model = settings.model
    _check_daily_block(model)

    est_in = _estimate_tokens(messages, tools)
    tpm = GROQ_LIMITS["tpm"]
    if est_in + 200 > tpm:
        raise LLMTooLargeError(
            f"This request is about {est_in:,} tokens, more than Groq's free tier allows in one "
            f"request ({tpm:,} tokens/minute). Use a smaller input or switch to Claude."
        )
    out_cap = max(200, min(max_tokens, GROQ_MAX_OUT, int(tpm * 0.95) - est_in))
    reserve = est_in + min(out_cap, 800)

    kwargs: dict[str, Any] = dict(
        model=model, messages=messages, max_completion_tokens=out_cap, temperature=0
    )
    if model.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = "low"   # reasoning tokens count against the same limits
    if response_format:
        kwargs["response_format"] = response_format
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    client = _groq_client(settings.groq_api_key)
    window = _window(model)

    for attempt in range(MAX_RATE_RETRIES):
        event = window.acquire(reserve)
        try:
            response = client.chat.completions.create(**kwargs)
        except groq.RateLimitError as exc:
            window.release(event)
            text = str(exc).lower()
            if "per day" in text or "tpd" in text or "rpd" in text:
                _daily_block[model] = date.today().isoformat()
                raise LLMRateLimitError(
                    "The Groq daily limit for this model has been reached "
                    f"({GROQ_LIMITS['rpd']:,} requests / {GROQ_LIMITS['tpd']:,} tokens per day). "
                    "Try again after it resets, pick another Groq model, or switch to Claude "
                    "in the AI Model panel.",
                    daily=True,
                ) from exc
            wait = _retry_after_seconds(exc) or 10 * (attempt + 1)
            _notify(f"Groq rate limit reached; retrying in ~{int(wait)}s…")
            time.sleep(min(wait + 0.5, 90))
            continue
        except groq.AuthenticationError as exc:
            window.release(event)
            raise LLMAuthError("Groq rejected the API key. Check GROQ_API_KEY in backend/.env.") from exc
        except groq.BadRequestError as exc:
            window.release(event)
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

        total = getattr(getattr(response, "usage", None), "total_tokens", None) or reserve
        window.correct(event, int(total))
        _record_usage(model, int(total))
        return response

    raise LLMRateLimitError(
        "Groq kept rate-limiting this request. Wait a minute and try again, or switch to Claude.",
        daily=False,
    )


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
            raise LLMError(
                "The model's answer was cut off before it finished (output limit reached). "
                "Try fewer items at a time or switch to Claude."
            )
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
    return (response.choices[0].message.content or "").strip()


def _groq_tool_loop(
    settings: Settings,
    system: str,
    history: list[dict],
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_rounds: int,
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

    for _ in range(max_rounds):
        response = _groq_chat(settings, messages, max_tokens=1500, tools=oa_tools)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            return (message.content or "").strip()

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
    return None


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


def _ollama_request(method: str, path: str, payload: Optional[dict] = None, timeout: float = 10.0):
    base = _ollama_base()
    try:
        # trust_env=False: never send local traffic through a corporate proxy
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0), trust_env=False) as client:
            response = client.request(method, base + path, json=payload)
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
) -> dict:
    model = settings.model
    info = _ollama_model_info(model) or {}
    ctx = min(OLLAMA_NUM_CTX, info.get("context") or OLLAMA_NUM_CTX)
    est_in = _estimate_tokens(messages, tools)
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

    data = _ollama_request("POST", "/api/chat", payload, timeout=OLLAMA_TIMEOUT)
    _record_usage(model, int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0))
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
            raise LLMError(
                "The local model's answer was cut off before it finished (output limit reached). "
                "Try fewer items at a time, pick a larger model, or switch to Claude."
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
    return _ollama_content(_ollama_chat(settings, messages, max_tokens=max_tokens))


def _ollama_tool_loop(
    settings: Settings,
    system: str,
    history: list[dict],
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_rounds: int,
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

    for _ in range(max_rounds):
        data = _ollama_chat(settings, messages, max_tokens=1500, tools=oa_tools)
        message = data.get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            return _ollama_content(data)

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
    return None


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
    response = _anthropic_client().messages.parse(
        model=settings.model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
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
    return response.content[0].text.strip()


@_anthropic_errors
def _anthropic_tool_loop(
    settings: Settings,
    system: str,
    history: list[dict],
    tools: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_rounds: int,
) -> Optional[str]:
    client = _anthropic_client()
    messages: list[dict[str, Any]] = list(history)

    for _ in range(max_rounds):
        response = client.messages.create(
            model=settings.model, max_tokens=1500, system=system, messages=messages, tools=tools
        )
        _record_usage(settings.model, _usage_of(response))

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if hasattr(b, "text")).strip()

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
    return None


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
) -> Optional[str]:
    """Let the model call *tools* (Anthropic-format schemas) until it answers.
    *execute_tool(name, input)* runs a tool and returns its result as text. Returns the final
    reply, or None if *max_rounds* tool rounds were used without a final answer."""
    s = _ready(settings)
    if s.provider == OLLAMA:
        return _ollama_tool_loop(s, system, messages, tools, execute_tool, max_rounds)
    if s.provider == GROQ:
        return _groq_tool_loop(s, system, messages, tools, execute_tool, max_rounds)
    return _anthropic_tool_loop(s, system, messages, tools, execute_tool, max_rounds)


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
