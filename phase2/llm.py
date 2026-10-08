"""One auditable GLM-5.1 request with a conservative pre-call budget reservation.

OpenRouter provider.max_price is denominated in USD per million tokens. Its
provider filter plus max_tokens bound the request; UTF-8 bytes conservatively
bound text input tokens. Unknown or failed accounting retains the reservation.
No automatic retries are made, including after an ambiguous transport failure.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

AUTHOR_MODEL = "z-ai/glm-5.1"
PROMPT_USD_PER_MILLION = 1.50
COMPLETION_USD_PER_MILLION = 4.50
BYOK_FEE_MULTIPLIER = 1.05


def author_timeout_sec() -> float:
    """Wall-clock allowance for an author/reviewer request, separate from solving."""
    try:
        timeout = float(os.environ.get("TASKLAB_AUTHOR_TIMEOUT_SEC", "180"))
    except ValueError as exc:
        raise ValueError("TASKLAB_AUTHOR_TIMEOUT_SEC must be a positive number up to 3600.") from exc
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError("TASKLAB_AUTHOR_TIMEOUT_SEC must be a positive number up to 3600.")
    return timeout


def redact(value: Any, api_key: str = "") -> Any:
    if isinstance(value, str):
        if api_key:
            value = value.replace(api_key, "[REDACTED]")
        return re.sub(r"sk-or-v1-[A-Za-z0-9_-]+", "[REDACTED]", value)
    if isinstance(value, dict):
        return {redact(k, api_key): redact(v, api_key) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, api_key) for v in value]
    return value


def _write(path: Path, value, api_key: str) -> None:
    path.write_text(json.dumps(redact(value, api_key), indent=2, ensure_ascii=False) + "\n")


def input_token_bound(messages: list[dict]) -> int:
    """Text-only requests: one token per UTF-8 byte plus generous chat framing."""
    for message in messages:
        if not isinstance(message.get("content"), str):
            raise ValueError("Budgeted authoring supports only text messages.")
    return len(json.dumps(messages, ensure_ascii=False).encode()) + 1024 + len(messages) * 64


def _nonnegative(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def usage_accounting(response: dict, reserved_usd: float) -> dict:
    usage = response.get("usage") or {}
    account = _nonnegative(usage.get("cost"))
    details = usage.get("cost_details") or {}
    upstream = _nonnegative(details.get("upstream_inference_cost"))
    if usage.get("is_byok") is True:
        actual = account + upstream if account is not None and upstream is not None else None
    elif usage.get("is_byok") is not False and upstream is not None:
        # Compatible responses sometimes omit the flag. Retain both charge
        # components rather than mistaking the account amount for total spend.
        actual = account + upstream if account is not None else None
    elif account is not None and (account > 0 or upstream in (0, None)):
        actual = account
    elif upstream is not None:
        actual = (account or 0) + upstream
    else:
        actual = None
    return {
        "source": "provider_usage" if actual is not None else "reserved_unknown_usage",
        "actual_cost_usd": actual,
        "cost_usd": actual if actual is not None else reserved_usd,
        "reserved_usd": reserved_usd, "openrouter_cost_usd": account,
        "upstream_cost_usd": upstream, "usage": usage,
    }


def budgeted_json_call(messages: list[dict], evidence_dir: str | Path, budget_usd: float,
                       max_tokens: int = 8192, temperature: float = 0.3,
                       request_name: str = "call", *, client=None,
                       api_key: str | None = None, base_url: str | None = None,
                       reasoning_enabled: bool | None = None) -> dict:
    """Return status/content/data/cost_usd, preserving each request and response.

    ``budget_usd`` is this call's remaining allowance. The caller subtracts
    ``cost_usd`` (actual or the retained reservation) before making another call.
    ``client`` permits a caller to share its no-retry OpenAI client.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", request_name):
        raise ValueError("Invalid evidence request name.")
    if _nonnegative(budget_usd) is None or not isinstance(max_tokens, int) or max_tokens < 1:
        raise ValueError("A nonnegative budget and positive max_tokens are required.")
    if os.environ.get("GENERATOR_MODEL", AUTHOR_MODEL) != AUTHOR_MODEL:
        raise ValueError(f"Task authoring is restricted to {AUTHOR_MODEL}.")
    timeout = author_timeout_sec()
    api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for task authoring.")
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    request_path = evidence / f"{request_name}-request.json"
    if request_path.exists():
        raise FileExistsError(f"Evidence already exists: {request_name}")
    tokens = input_token_bound(messages)
    prompt_reserve = tokens * PROMPT_USD_PER_MILLION / 1_000_000
    completion_allowance = (budget_usd / BYOK_FEE_MULTIPLIER - prompt_reserve)
    allowed_tokens = min(max_tokens, math.floor(completion_allowance * 1_000_000 / COMPLETION_USD_PER_MILLION))
    result = {
        "status": "budget_exhausted", "model": AUTHOR_MODEL, "content": "", "data": None,
        "cost_usd": 0.0, "reserved_usd": 0.0, "finish_reason": None,
        "budget_usd": budget_usd, "input_token_bound": tokens,
        "evidence_dir": str(evidence), "request_name": request_name,
        "error": None,
        "request_timeout_sec": timeout,
    }
    # A heavily truncated authoring response is not useful; stop before paying.
    if allowed_tokens < min(1024, max_tokens):
        result["error"] = "Insufficient remaining budget for a useful response."
        _write(evidence / f"{request_name}-result.json", result, api_key)
        return result
    reserved = (prompt_reserve + allowed_tokens * COMPLETION_USD_PER_MILLION / 1_000_000) * BYOK_FEE_MULTIPLIER
    result.update(reserved_usd=reserved, cost_usd=reserved)
    request = {
        "model": AUTHOR_MODEL, "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": allowed_tokens, "temperature": temperature,
        "extra_body": {
            "provider": {
                "sort": "throughput",
                "max_price": {"prompt": PROMPT_USD_PER_MILLION,
                              "completion": COMPLETION_USD_PER_MILLION,
                              "request": 0},
                "require_parameters": True,
            },
            "usage": {"include": True},
        },
    }
    if reasoning_enabled is not None:
        request["extra_body"]["reasoning"] = {"enabled": reasoning_enabled}
    _write(request_path, request, api_key)
    _write(evidence / f"{request_name}-reservation.json", result, api_key)
    own_client = client is None
    started = time.monotonic()
    try:
        if own_client:
            client = OpenAI(api_key=api_key, base_url=base_url or os.environ.get(
                "OPENROUTER_API_BASE", "https://api.aqinference.com/v1"),
                max_retries=0, timeout=timeout)
        try:
            response = client.chat.completions.create(**request)
        except Exception as exc:
            error = {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
            _write(evidence / f"{request_name}-api-error.json", error, api_key)
            result.update(status="api_error", error=error,
                          accounting={"source": "reserved_ambiguous_request", "cost_usd": reserved})
            return result
        raw = response.model_dump(mode="json")
        _write(evidence / f"{request_name}-response.json", raw, api_key)
        accounting = usage_accounting(raw, reserved)
        result.update(accounting=accounting, cost_usd=accounting["cost_usd"])
        choices = raw.get("choices") or []
        choice = choices[0] if choices else {}
        result["finish_reason"] = choice.get("finish_reason")
        result["content"] = redact((choice.get("message") or {}).get("content") or "", api_key)
        try:
            if result["finish_reason"] != "stop":
                raise ValueError("Response did not finish normally.")
            parsed = json.loads(result["content"])
            if not isinstance(parsed, dict):
                raise ValueError("Response must be a JSON object.")
            result.update(status="completed", data=parsed)
        except (ValueError, TypeError):
            result.update(status="invalid_response", error="Incomplete response or invalid JSON object.")
        if accounting["cost_usd"] > budget_usd + 1e-9:
            result.update(status="budget_exhausted", error="Provider-reported usage exceeded the reserved budget.")
        return result
    finally:
        if own_client and client is not None:
            client.close()
        result["elapsed_sec"] = round(time.monotonic() - started, 3)
        _write(evidence / f"{request_name}-result.json", result, api_key)
