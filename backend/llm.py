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
"""

import copy
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

import anthropic
from pydantic import BaseModel, ValidationError

from config import (
    ANTHROPIC_API_KEY,
    GROQ_API_KEY,
    LLM_SETTINGS_FILE,
    LLM_USAGE_FILE,
    SCORING_MODEL,
)

logger = logging.getLogger(__name__)

CLAUDE = "anthropic"
GROQ = "groq"

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


def load_settings() -> Settings:
    data: dict = {}
    if LLM_SETTINGS_FILE.exists():
        try:
            data = json.loads(LLM_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Could not read %s; using defaults.", LLM_SETTINGS_FILE)
    provider = data.get("provider") if data.get("provider") in (CLAUDE, GROQ) else CLAUDE
    if provider == GROQ:
        model = data.get("model") if data.get("model") in GROQ_MODEL_IDS else GROQ_MODEL_IDS[0]
    else:
        model = SCORING_MODEL
    # The Groq key comes only from backend/.env (GROQ_API_KEY) -- the app never stores it.
    return Settings(provider, model, (GROQ_API_KEY or "").strip())


def save_settings(provider: str, model: str) -> Settings:
    """Validate and persist the provider/model selection. API keys are not handled
    here: they are read from backend/.env only."""
    if provider not in (CLAUDE, GROQ):
        raise ValueError(f"Unknown provider '{provider}'.")
    if provider == GROQ and model not in GROQ_MODEL_IDS:
        raise ValueError(f"Unsupported Groq model '{model}'.")
    stored_model = model if provider == GROQ else SCORING_MODEL
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
    return f"Claude · {s.model}"


def public_settings() -> dict:
    """Settings for the UI. API keys are never returned, only whether each is set in backend/.env."""
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
    }


def not_configured_message(settings: Optional[Settings] = None) -> Optional[str]:
    """None if the active provider can be used, otherwise a message telling the user what to do."""
    s = settings or load_settings()
    if s.provider == GROQ:
        if not s.groq_api_key:
            return "GROQ_API_KEY is not set. Add it to backend/.env and restart the backend."
        return None
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY is not set. Add it to backend/.env and restart the backend."
    return None


def budget(settings: Optional[Settings] = None) -> dict:
    """Prompt-size caps. Claude values equal the app's original limits; Groq values are
    smaller so a single request stays well inside the 8,000 tokens/minute free-tier cap."""
    s = settings or load_settings()
    if s.provider == GROQ:
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
    """Parallel requests for batch steps. Groq's per-minute cap makes more than a couple pointless."""
    s = settings or load_settings()
    return 2 if s.provider == GROQ else 8


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
    if s.provider == GROQ:
        return _groq_structured(s, system, user, schema, max_tokens)
    return _anthropic_structured(s, system, user, schema, max_tokens)


def text(
    system: str, user: str, max_tokens: int = 1024, settings: Optional[Settings] = None
) -> str:
    """One call returning plain text."""
    s = _ready(settings)
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
