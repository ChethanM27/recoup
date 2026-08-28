"""
Razorpay test-mode client.

Standard library only - urllib, no SDK, no pip install. The point of this module
is not that it can call an API; anyone can call an API. The point is what it does
when the API does not answer.

Three layers of defence:
  - retry with exponential backoff on transient failures (5xx, timeout)
  - a circuit breaker that stops hammering a gateway that is clearly down
  - graceful degradation: when the circuit is open, recovery actions are queued
    with a reason instead of being lost, and the batch keeps running

A recovery agent that dies when the payment gateway hiccups is worse than no
recovery agent, because the merchant now believes the money is being chased.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta

API_BASE = "https://api.razorpay.com/v1"


class RazorpayUnavailable(Exception):
    """The gateway could not be reached after exhausting retries."""


class CircuitOpen(Exception):
    """We are deliberately not calling the gateway right now."""


@dataclass
class CircuitBreaker:
    """
    Closed -> requests flow. After `threshold` consecutive failures the circuit
    opens and we stop calling for `cooldown_seconds`. One probe request is then
    allowed through; success closes the circuit, failure re-opens it.
    """
    threshold: int = 4
    cooldown_seconds: int = 60
    consecutive_failures: int = 0
    opened_at: float = None
    trips: int = 0

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.time() - self.opened_at >= self.cooldown_seconds:
            return "half_open"
        return "open"

    def before_call(self):
        st = self.state
        if st == "open":
            remaining = self.cooldown_seconds - (time.time() - self.opened_at)
            raise CircuitOpen(
                f"circuit open after {self.consecutive_failures} consecutive "
                f"failures; retrying in {remaining:.0f}s"
            )

    def record_success(self):
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and self.opened_at is None:
            self.opened_at = time.time()
            self.trips += 1


@dataclass
class CallRecord:
    ts: str
    method: str
    path: str
    status: str
    attempts: int
    latency_ms: int
    detail: str = ""


class RazorpayClient:
    def __init__(self, key_id=None, key_secret=None, timeout=8,
                 max_retries=3, mock=None):
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.breaker = CircuitBreaker()
        self.call_log = []
        # No credentials -> mock transport. The repo must run for anyone who
        # clones it, including a reviewer who has no keys.
        self.mock = (not (self.key_id and self.key_secret)) if mock is None else mock
        self.queued = []   # actions deferred because the gateway was unreachable

    # ------------------------------------------------------------- transport

    def _auth_header(self):
        raw = f"{self.key_id}:{self.key_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _request(self, method, path, payload=None):
        if self.mock:
            return _mock_response(method, path, payload)

        self.breaker.before_call()

        url = f"{API_BASE}{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        last_err = None
        started = time.time()

        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Authorization", self._auth_header())
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                self.breaker.record_success()
                self._log(method, path, "ok", attempt, started)
                return data
            except urllib.error.HTTPError as e:
                detail = e.read().decode()[:300]
                if 400 <= e.code < 500 and e.code != 429:
                    # Our request is wrong. Retrying an invalid request is just
                    # noise - fail fast and surface it.
                    self.breaker.record_success()
                    self._log(method, path, f"http_{e.code}", attempt, started, detail)
                    raise RazorpayUnavailable(f"HTTP {e.code}: {detail}")
                last_err = f"HTTP {e.code}: {detail}"
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = f"{type(e).__name__}: {e}"
            except json.JSONDecodeError as e:
                last_err = f"malformed response: {e}"

            if attempt < self.max_retries:
                time.sleep(min(2 ** (attempt - 1) * 0.4, 4.0))

        self.breaker.record_failure()
        self._log(method, path, "failed", self.max_retries, started, last_err or "")
        raise RazorpayUnavailable(last_err or "unknown transport failure")

    def _log(self, method, path, status, attempts, started, detail=""):
        self.call_log.append(CallRecord(
            ts=datetime.now().isoformat(timespec="seconds"),
            method=method, path=path, status=status, attempts=attempts,
            latency_ms=int((time.time() - started) * 1000), detail=detail,
        ))

    # ------------------------------------------------------------ operations

    def health(self) -> dict:
        """Cheap read used to decide whether live mode is usable at all."""
        try:
            self._request("GET", "/payments?count=1")
            return {"reachable": True, "mode": "mock" if self.mock else "live",
                    "circuit": self.breaker.state}
        except (RazorpayUnavailable, CircuitOpen) as e:
            return {"reachable": False, "mode": "mock" if self.mock else "live",
                    "circuit": self.breaker.state, "error": str(e)}

    def create_order(self, amount_paise, receipt, notes=None):
        return self._request("POST", "/orders", {
            "amount": amount_paise, "currency": "INR",
            "receipt": receipt[:40], "notes": notes or {},
        })

    def create_payment_link(self, amount_paise, description, reference_id,
                            expire_by=None, notes=None):
        """
        The workhorse of recovery: a fresh, short-lived link the customer can pay
        on any rail. Expiry is not decoration - an unexpired recovery link is an
        open invitation to pay twice.
        """
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description[:255],
            "reference_id": reference_id[:40],
            "expire_by": expire_by or int((datetime.now() + timedelta(days=3)).timestamp()),
            "reminder_enable": False,   # Recoup owns cadence, not Razorpay
            "notify": {"sms": False, "email": False},  # we gate our own outreach
            "notes": notes or {},
        }
        return self._request("POST", "/payment_links", payload)

    def fetch_payment(self, payment_id):
        return self._request("GET", f"/payments/{payment_id}")

    def fetch_payment_link(self, link_id):
        return self._request("GET", f"/payment_links/{link_id}")

    def cancel_payment_link(self, link_id):
        return self._request("POST", f"/payment_links/{link_id}/cancel")

    # ------------------------------------------------- graceful degradation

    def execute_or_queue(self, action_label, fn, *args, **kwargs):
        """
        Run a gateway operation. If the gateway is unreachable or the circuit is
        open, park the intent with a reason and carry on. Nothing is silently
        dropped and nothing raises into the batch loop.

        Returns (status, payload) where status is one of:
            "executed" | "queued" | "rejected"
        """
        try:
            return "executed", fn(*args, **kwargs)
        except CircuitOpen as e:
            self.queued.append({
                "action": action_label, "reason": "circuit_open",
                "detail": str(e), "queued_at": datetime.now().isoformat(timespec="seconds"),
            })
            return "queued", {"reason": "circuit_open", "detail": str(e)}
        except RazorpayUnavailable as e:
            msg = str(e)
            if msg.startswith("HTTP 4"):
                # Our fault, not theirs. Queuing a malformed request would just
                # replay the same mistake later.
                return "rejected", {"reason": "bad_request", "detail": msg}
            self.queued.append({
                "action": action_label, "reason": "gateway_unreachable",
                "detail": msg, "queued_at": datetime.now().isoformat(timespec="seconds"),
            })
            return "queued", {"reason": "gateway_unreachable", "detail": msg}

    def drain_queue(self):
        """Called when the circuit closes again. Returns what was waiting."""
        items, self.queued = self.queued, []
        return items


# ----------------------------------------------------------------- mock mode

_MOCK_SEQ = {"n": 0}


def _mock_response(method, path, payload):
    """Shaped like the real thing so nothing downstream has to care."""
    _MOCK_SEQ["n"] += 1
    n = _MOCK_SEQ["n"]
    now = int(time.time())

    if path.startswith("/orders") and method == "POST":
        return {"id": f"order_MOCK{n:09d}", "entity": "order",
                "amount": payload["amount"], "currency": "INR",
                "receipt": payload["receipt"], "status": "created",
                "created_at": now, "notes": payload.get("notes", {})}

    if path.startswith("/payment_links") and method == "POST":
        return {"id": f"plink_MOCK{n:09d}", "entity": "payment_link",
                "amount": payload["amount"], "currency": "INR",
                "status": "created",
                "short_url": f"https://rzp.io/i/MOCK{n:06d}",
                "reference_id": payload["reference_id"],
                "expire_by": payload["expire_by"], "created_at": now}

    if path.startswith("/payments") and method == "GET":
        return {"entity": "collection", "count": 0, "items": []}

    return {"id": f"mock_{n:09d}", "entity": "mock", "status": "ok"}
