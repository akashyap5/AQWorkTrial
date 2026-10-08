"""Per-trial, text-only OpenRouter gateway with admission-time reservations.

No provider request is sent until its conservative token/price bound fits. An
ambiguous transport/usage result retains the reservation and closes admission.
Provider-reported upstream BYOK spend is included, even when account cost is 0.
This is an inference allowance, not a cap on taxes or credit-purchase fees.
"""
import copy
import json
import math
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import httpx

from runner.store import MODEL, now, read_json, write_json


class BudgetStop(Exception):
    pass


def reported_cost(response):
    usage = response.get("usage") or {}
    cost = usage.get("cost")
    upstream = (usage.get("cost_details") or {}).get("upstream_inference_cost")
    numbers = [float(v) for v in (cost, upstream) if v is not None and not isinstance(v, bool)]
    if (not numbers or any(isinstance(v, bool) for v in (cost, upstream))
            or any(not math.isfinite(v) or v < 0 for v in numbers)):
        raise ValueError("Provider response is missing valid cost accounting")
    if usage.get("is_byok") is False:
        if cost is None:
            raise ValueError("Non-BYOK response is missing the OpenRouter charge")
        # This field can be an informational duplicate of usage.cost for ordinary
        # OpenRouter billing. It is an additional invoice only when using BYOK.
        return float(cost)
    if usage.get("is_byok") is True and (cost is None or upstream is None):
        raise ValueError("BYOK accounting requires both account and upstream costs")
    # When the flag is omitted, conservatively retain both known charges.
    return sum(numbers)


def reconcile_completed_ledger(path, *, apply=False):
    """Explicit, audited correction of old non-BYOK duplicate charges.

    Invoke only after the owning batch/runner is stopped. The default is a
    read-only report. Pending requests prohibit writes; unknown reservations
    remain intact. Original provider usage and every old/new amount are kept.
    """
    path = Path(path)
    original = read_json(path)
    if original is None:
        raise ValueError("Missing budget ledger")
    amounts = [original["spent_usd"], original["reserved_usd"]]
    amounts += [entry[key] for entry in original["requests"] for key in ("cost_usd", "reserved_usd") if key in entry]
    if any(isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0 for value in amounts):
        raise ValueError("Invalid ledger amounts; manual review required")
    if any(r["status"] == "pending" for r in original["requests"]):
        raise ValueError("Ledger still has a pending provider request")
    data = copy.deepcopy(original)
    changes = []
    old_total = 0.0
    new_total = 0.0
    unresolved_reservations = 0.0
    recovered_delivery_errors = []
    for index, entry in enumerate(data["requests"]):
        delivery_mislabel = (entry["status"] == "unknown"
                            and entry.get("error") in {"BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError"}
                            and "cost_usd" in entry and isinstance(entry.get("usage"), dict)
                            and bool(entry.get("generation_id")))
        if entry["status"] != "settled" and not delivery_mislabel:
            if entry["status"] == "unknown":
                unresolved_reservations += float(entry["reserved_usd"])
            continue
        old = float(entry["cost_usd"])
        new = reported_cost({"usage": entry["usage"]})
        old_total += old
        new_total += new
        if abs(old - new) > 1e-12 or delivery_mislabel:
            change = {"request_index": index, "generation_id": entry.get("generation_id"),
                      "old_cost_usd": old, "new_cost_usd": new}
            if delivery_mislabel:
                change.update(old_status="unknown", new_status="settled", original_error=entry["error"])
                recovered_delivery_errors.append(index)
                entry["delivery_error"] = entry.pop("error")
                entry["status"] = "settled"
            changes.append(change)
            entry["cost_usd"] = new
    if abs(old_total - original["spent_usd"]) > 1e-9:
        raise ValueError("Ledger total does not match its settled requests; manual review required")
    if abs(unresolved_reservations - original["reserved_usd"]) > 1e-9:
        raise ValueError("Ledger reservations do not match unresolved requests; manual review required")
    report = {"ledger": str(path), "old_spent_usd": old_total, "new_spent_usd": new_total,
              "reserved_usd_unchanged": original["reserved_usd"], "changes": changes,
              "applied": False}
    if apply and changes:
        # Detect an accidentally active ledger rather than overwriting new usage.
        if read_json(path) != original:
            raise ValueError("Ledger changed during reconciliation")
        data["spent_usd"] = new_total
        if recovered_delivery_errors and not any(r["status"] == "unknown" for r in data["requests"]):
            old_error = data.get("accounting_error")
            if old_error == "Unresolved request; reservation retained and further requests blocked":
                data["accounting_error"] = None
                report["accounting_error_correction"] = {"old": old_error, "new": None}
        data.setdefault("accounting_adjustments", []).append({
            "at": now(), "reason": "Reconcile non-BYOK charges and proven response-delivery mislabels from original usage",
            **{k: v for k, v in report.items() if k not in {"ledger", "applied"}}})
        write_json(path, data)
        report["applied"] = True
    return report


class Ledger:
    def __init__(self, path, budget):
        self.path, self.budget = path, budget
        self.lock = threading.Lock()
        self.data = read_json(path) or {
            "limit_usd": budget["cost_usd"], "spent_usd": 0.0,
            "reserved_usd": 0.0, "requests": [], "stop_reason": None,
            "accounting_error": None, "created_at": now()}
        # A request in flight at a restart cannot safely be retried.
        if self.data["reserved_usd"]:
            self.data["accounting_error"] = "Interrupted request has unresolved provider cost"
        self.save()

    def save(self):
        write_json(self.path, self.data)

    def prepare(self, original):
        with self.lock:
            if self.data["accounting_error"]:
                raise ValueError(self.data["accounting_error"])
            if self.data["stop_reason"]:
                raise BudgetStop(self.data["stop_reason"])
            payload = copy.deepcopy(original)
            if payload.get("model") != MODEL.removeprefix("openrouter/"):
                raise ValueError("Metered gateway accepts only the fixed evaluator model")
            if payload.get("stream") or payload.get("n", 1) != 1:
                raise ValueError("Metered gateway requires one non-streaming completion")
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("Expected text messages")
            for message in messages:
                content = message.get("content")
                if content is not None and not isinstance(content, str):
                    raise ValueError("Metered gateway supports text-only requests")
            # Disallow service-side tools and transforms with unbounded extra work.
            if any(payload.get(k) for k in ("plugins", "transforms", "web_search_options", "modalities", "audio")):
                raise ValueError("Unmetered server-side features are not supported")
            # UTF-8 bytes overestimate byte-fallback token counts. Double the full
            # serialized body and add message/template overhead for a conservative
            # bound; provider-reported tokens are checked against it afterwards.
            input_bound = 2 * len(json.dumps(payload, ensure_ascii=False).encode()) + 1024 + 256 * len(messages)
            rates = self.budget["max_price_per_million"]
            remaining = self.data["limit_usd"] - self.data["spent_usd"] - self.data["reserved_usd"]
            # 10% headroom covers the documented BYOK service fee (currently 5%).
            input_cost = input_bound * rates["prompt"] / 1_000_000 * 1.1
            affordable = math.floor((remaining - input_cost) / (rates["completion"] / 1_000_000 * 1.1))
            requested = min(int(payload.get("max_tokens") or payload.get("max_completion_tokens") or self.budget["max_completion_tokens"]),
                            self.budget["max_completion_tokens"])
            maximum = min(requested, affordable)
            if maximum < 1024:
                self.data["stop_reason"] = "cost"
                self.save()
                raise BudgetStop("cost")
            reservation = input_cost + maximum * rates["completion"] / 1_000_000 * 1.1
            payload.pop("max_completion_tokens", None)
            payload["max_tokens"] = maximum
            payload["provider"] = {"max_price": {**rates, "request": 0}, "require_parameters": True}
            if self.budget.get("provider_sort"):
                payload["provider"]["sort"] = self.budget["provider_sort"]
            payload["usage"] = {"include": True}
            entry = {"started_at": now(), "input_token_bound": input_bound,
                     "max_completion_tokens": maximum, "reserved_usd": reservation,
                     "requested_completion_tokens": requested,
                     "cost_limited_completion": maximum < requested,
                     "status": "pending"}
            self.data["reserved_usd"] += reservation
            self.data["requests"].append(entry)
            self.save()
            return payload, len(self.data["requests"]) - 1

    def settle(self, index, response=None, error=None):
        with self.lock:
            entry = self.data["requests"][index]
            if entry["status"] != "pending":
                return  # Billing has already been reconciled or conservatively retained.
            if error:
                entry.update(status="unknown", error=str(error), completed_at=now())
                self.data["accounting_error"] = "Unresolved request; reservation retained and further requests blocked"
            else:
                try:
                    cost = reported_cost(response)
                    usage = response.get("usage") or {}
                    if int(usage.get("prompt_tokens", 0)) > entry["input_token_bound"]:
                        raise ValueError("Provider prompt token count exceeded the conservative bound")
                    if int(usage.get("completion_tokens", 0)) > entry["max_completion_tokens"]:
                        raise ValueError("Provider completion token count exceeded the requested limit")
                    if cost > entry["reserved_usd"] + 1e-8:
                        raise ValueError("Provider charge exceeded the reserved price bound")
                except (ValueError, TypeError) as exc:
                    entry.update(status="unknown", completed_at=now(), error=str(exc))
                    self.data["accounting_error"] = str(exc)
                else:
                    self.data["reserved_usd"] -= entry["reserved_usd"]
                    self.data["reserved_usd"] = max(0.0, self.data["reserved_usd"])
                    self.data["spent_usd"] += cost
                    entry.update(status="settled", cost_usd=cost, usage=usage,
                                 generation_id=response.get("id"), completed_at=now())
                    entry["finish_reasons"] = [choice.get("finish_reason") for choice in response.get("choices", [])]
                    if entry.get("cost_limited_completion") and "length" in entry["finish_reasons"]:
                        # A solver truncated by our allowance is inconclusive,
                        # even if it exits without requesting another turn.
                        self.data["stop_reason"] = "cost"
            self.save()


class BudgetProxy:
    """Ephemeral loopback listener. A worker crash closes admission, never bypasses it."""
    def __init__(self, path, budget, upstream_base, transport=None):
        parsed = urlparse(upstream_base)
        if parsed.scheme != "https" or parsed.hostname != "openrouter.ai" or parsed.path.rstrip("/") != "/api/v1":
            raise ValueError("Cost enforcement requires https://openrouter.ai/api/v1")
        self.ledger = Ledger(path, budget)
        self.key = "sk-or-v1-local-" + secrets.token_urlsafe(32)
        self.client = httpx.Client(timeout=budget["agent_timeout_sec"] + 10, transport=transport)
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, payload):
                content = json.dumps(payload).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except OSError:
                    pass  # A disconnected solver cannot change settled provider billing.

            def do_POST(self):
                if self.headers.get("Authorization") != f"Bearer {proxy.key}":
                    return self.respond(401, {"error": {"message": "Invalid trial credential"}})
                if self.path != "/v1/chat/completions":
                    return self.respond(404, {"error": {"message": "Unsupported endpoint"}})
                index = None
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 2_000_000:
                        raise ValueError("Invalid request length")
                    payload, index = proxy.ledger.prepare(json.loads(self.rfile.read(length)))
                    response = proxy.client.post(upstream_base.rstrip("/") + "/chat/completions", json=payload,
                        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]})
                    if response.status_code != 200:
                        proxy.ledger.settle(index, error=f"Upstream HTTP {response.status_code}")
                        # Do not disclose upstream responses, credentials, or retry
                        # ambiguous paid work automatically.
                        return self.respond(502, {"error": {"message": f"Upstream HTTP {response.status_code}; inspect budget ledger"}})
                    data = response.json()
                    proxy.ledger.settle(index, data)
                    if proxy.ledger.data["accounting_error"]:
                        return self.respond(502, {"error": {"message": proxy.ledger.data["accounting_error"]}})
                    return self.respond(200, data)
                except BudgetStop:
                    return self.respond(402, {"error": {"message": "Task Lab cost budget exhausted", "code": "budget_exhausted"}})
                except Exception as exc:
                    if index is not None:
                        proxy.ledger.settle(index, error=type(exc).__name__)
                    return self.respond(400, {"error": {"message": "Task Lab blocked request: " + type(exc).__name__}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        # Requests still in flight can finish accounting after an agent timeout;
        # keep their conservative reservation until then.
        if not any(r["status"] == "pending" for r in self.ledger.data["requests"]):
            self.client.close()
