"""
The language model layer, and the cage around it.

Recoup uses an LLM for exactly two jobs:

  1. DIAGNOSIS OF THE LONG TAIL. Deterministic rules classify ~93% of failures
     with high confidence. The model is only consulted on the residue - error
     signatures no rule matched. Where a rule fires, the rule wins and the model
     is not called. Spending a model call to re-derive `card_expired ->
     INSTRUMENT_DEAD` is not intelligence, it is latency.

  2. CUSTOMER-FACING COPY. Hinglish outreach that sounds like a person, tuned to
     the specific root cause.

Everything the model returns is untrusted input. It passes through a validator
before it can influence a single rupee:

  - a proposed root cause must be one of the eight known causes
  - a proposed action must appear in that cause's playbook AND clear every gate
  - proposed copy must not invent amounts, deadlines, penalties or guarantees,
    must carry an opt-out, and must stay inside length limits

Rejections are logged with the reason. The model gets a vote, never a veto, and
never the last word.
"""

import json
import os
import re
import urllib.error
import urllib.request

from .taxonomy import RootCause, Action, PLAYBOOKS

# Copy that must never reach a customer, regardless of how the model phrases it.
FORBIDDEN_COPY = [
    r"\blegal action\b", r"\bpolice\b", r"\bcourt\b", r"\bblacklist",
    r"\bcredit score\b", r"\bpenalt", r"\bfine\b", r"\blawyer\b",
    r"\bguarantee", r"\bcibil\b", r"\bdefault(er|ed)?\b", r"\brecovery agent\b",
    r"\bimmediately or\b", r"\blast (and )?final\b", r"\bfinal warning\b",
]
MAX_COPY_CHARS = 320

# A rule that matches a known decline code earns 0.97. Model output is capped
# below that on purpose: a confident hallucination must never be able to
# outrank a deterministic match, or clear a gate a rule would have failed.
# Named rather than inline so `redteam.py` tests the real value, not a copy.
LLM_CONFIDENCE_CEILING = 0.85


class LLMUnavailable(Exception):
    pass


# --------------------------------------------------------------- transport

class LLM:
    """
    provider: "gemini" | "anthropic" | "openai" | "none"
    With provider "none" (or no key) every call falls back to deterministic
    templates, so the repo runs end to end for a reviewer with no API key.
    """

    def __init__(self, provider=None, api_key=None, model=None, timeout=25):
        self.provider = (provider or os.environ.get("LLM_PROVIDER", "none")).lower()
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.timeout = timeout
        self.model = model or {
            "gemini": "gemini-2.0-flash",
            "anthropic": "claude-sonnet-4-6",
            "openai": "gpt-4o-mini",
        }.get(self.provider, "")
        if not self.api_key:
            self.provider = "none"
        self.calls = 0
        self.failures = 0

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    def complete(self, system: str, user: str) -> str:
        if not self.enabled:
            raise LLMUnavailable("no provider configured")
        self.calls += 1
        try:
            if self.provider == "gemini":
                return self._gemini(system, user)
            if self.provider == "anthropic":
                return self._anthropic(system, user)
            if self.provider == "openai":
                return self._openai(system, user)
        except Exception as e:
            self.failures += 1
            raise LLMUnavailable(f"{type(e).__name__}: {e}")
        raise LLMUnavailable(f"unknown provider {self.provider}")

    def _post(self, url, payload, headers):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def _gemini(self, system, user):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        data = self._post(url, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 700},
        }, {})
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def _anthropic(self, system, user):
        data = self._post("https://api.anthropic.com/v1/messages", {
            "model": self.model, "max_tokens": 700, "temperature": 0.2,
            "system": system, "messages": [{"role": "user", "content": user}],
        }, {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in data["content"])

    def _openai(self, system, user):
        data = self._post("https://api.openai.com/v1/chat/completions", {
            "model": self.model, "temperature": 0.2, "max_tokens": 700,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }, {"Authorization": f"Bearer {self.api_key}"})
        return data["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    """Models wrap JSON in prose and fences no matter how firmly you ask."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(t[start:end + 1])


# --------------------------------------------------------------- diagnosis

DIAGNOSIS_SYSTEM = """You are a payments failure analyst for an Indian merchant on Razorpay.
You are given a failed payment whose error signature matched no deterministic rule.
Assign it to exactly one root cause from this closed set:

TRANSIENT_RAIL     - gateway or issuer failed; customer did nothing wrong
INSUFFICIENT_FUNDS - account was short of money
AUTH_FRICTION      - customer willing but blocked at OTP/3DS
INSTRUMENT_DEAD    - card expired/blocked or VPA invalid; this instrument can never work
RISK_BLOCKED       - declined by risk, fraud or compliance
CUSTOMER_ABANDON   - customer cancelled or walked away
MANDATE_FAILURE    - subscription or e-mandate presentation failed
UNKNOWN            - you cannot tell

Rules you must follow:
- If you are not confident, answer UNKNOWN. An honest UNKNOWN routes to a human.
  A confident wrong answer causes us to contact a customer wrongly, at scale.
- Never choose RISK_BLOCKED to be safe, and never avoid it to be helpful.
  It has specific meaning: a risk or compliance system declined this.
- confidence is your own probability that the cause is correct, 0.0 to 1.0.

Reply with ONLY a JSON object, no prose and no code fences:
{"cause": "<one of the above>", "confidence": <float>, "rationale": "<one sentence>"}"""


def diagnose(llm: LLM, event) -> tuple:
    """
    Returns (RootCause, confidence, rationale, source) where source is
    "llm" or "fallback". Never raises.
    """
    if not llm.enabled:
        return RootCause.UNKNOWN, 0.0, "no model configured; routed to human", "fallback"

    user = json.dumps({
        "error_reason": event.error_reason,
        "error_source": event.error_source,
        "error_step": event.error_step,
        "error_code": event.error_code,
        "method": event.method,
        "amount_rupees": round(event.amount, 2),
        "attempt_no": event.attempt_no,
        "is_subscription": event.is_subscription,
        "customer_prior_failures": event.customer_prior_failures,
    }, indent=2)

    try:
        raw = llm.complete(DIAGNOSIS_SYSTEM, user)
        obj = _parse_json(raw)
    except (LLMUnavailable, ValueError, json.JSONDecodeError) as e:
        return RootCause.UNKNOWN, 0.0, f"model unavailable ({e}); routed to human", "fallback"

    # ---- cage ----
    name = str(obj.get("cause", "")).strip().upper()
    if name not in RootCause.__members__:
        return (RootCause.UNKNOWN, 0.0,
                f"model returned unrecognised cause '{name}'; rejected", "fallback")

    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    # The model never gets rule-level certainty. Deterministic matches sit at
    # 0.97; a model opinion is capped below that so it can never outrank a rule.
    conf = min(conf, LLM_CONFIDENCE_CEILING)

    rationale = str(obj.get("rationale", ""))[:240] or "model gave no rationale"
    return RootCause[name], conf, f"[llm] {rationale}", "llm"


# ------------------------------------------------------------------- copy

COPY_SYSTEM = """You write short recovery messages for an Indian merchant's customers,
in natural Hinglish (Roman script, the way people actually message in India).

Hard constraints:
- Under 280 characters.
- Warm and matter-of-fact. The customer is not a debtor; a payment just failed.
- State plainly what happened and give exactly one easy next step.
- NEVER mention legal action, police, credit score, CIBIL, penalties, fines,
  blacklisting, or being a defaulter. Never threaten. Never say "final warning".
- Never invent an amount, a deadline, a discount or a guarantee. Use only the
  amount given to you, and write it as Rs <amount>.
- End with a plain opt-out: "Reply STOP to opt out."
- Use {{link}} as a literal placeholder where the payment link goes. Do not
  invent a URL.

Reply with ONLY a JSON object, no prose and no code fences:
{"message": "<the message>"}"""

FALLBACK_COPY = {
    RootCause.TRANSIENT_RAIL: (
        "Hi! Aapka Rs {amt} ka payment bank side se fail ho gaya tha - aapki koi "
        "galti nahi. Yahan se ek click mein ho jayega: {{link}} Reply STOP to opt out."),
    RootCause.INSUFFICIENT_FUNDS: (
        "Hi! Rs {amt} ka payment complete nahi ho paya. Jab convenient ho, yahan se "
        "kar sakte hain: {{link}} Koi jaldi nahi. Reply STOP to opt out."),
    RootCause.AUTH_FRICTION: (
        "Hi! OTP step par aapka Rs {amt} ka payment atak gaya. UPI se try kijiye, "
        "OTP ki zaroorat nahi padegi: {{link}} Reply STOP to opt out."),
    RootCause.INSTRUMENT_DEAD: (
        "Hi! Jo card aapne use kiya tha wo ab valid nahi hai, isliye Rs {amt} ka "
        "payment nahi hua. Dusre method se yahan kar sakte hain: {{link}} "
        "Reply STOP to opt out."),
    RootCause.CUSTOMER_ABANDON: (
        "Hi! Aapka order abhi bhi reserved hai - Rs {amt}. Complete karna ho to "
        "yahan se: {{link}} Reply STOP to opt out."),
    RootCause.MANDATE_FAILURE: (
        "Hi! Aapka subscription ka Rs {amt} auto-debit is baar nahi ho paya. "
        "Ek baar manually kar dijiye taki service chalti rahe: {{link}} "
        "Reply STOP to opt out."),
}


def validate_copy(text: str, amount: float) -> tuple:
    """Returns (ok, cleaned_or_reason). Applied to model and template output alike."""
    if not text or not text.strip():
        return False, "empty message"
    t = " ".join(text.split())

    if len(t) > MAX_COPY_CHARS:
        return False, f"too long ({len(t)} chars, limit {MAX_COPY_CHARS})"

    low = t.lower()
    for pat in FORBIDDEN_COPY:
        if re.search(pat, low):
            return False, f"contains forbidden phrasing matching /{pat}/"

    if "{link}" not in t:
        return False, "missing {link} placeholder"

    if not re.search(r"\breply stop\b", low):
        return False, "missing opt-out instruction"

    # Any rupee figure in the copy must be the real one. A model that rounds
    # Rs 1,847 to "about Rs 1,800" has just quoted a price we cannot honour.
    figures = re.findall(r"(?:rs\.?|inr|\u20b9)\s*([\d,]+(?:\.\d+)?)", low)
    for f in figures:
        try:
            v = float(f.replace(",", ""))
        except ValueError:
            return False, f"unparseable amount '{f}'"
        if abs(v - round(amount)) > 1.0 and abs(v - amount) > 1.0:
            return False, f"quotes Rs {f} but the real amount is Rs {amount:.2f}"

    return True, t


def write_copy(llm: LLM, event, cause: RootCause) -> tuple:
    """
    Returns (message, source, note). Source is "llm", "fallback_rejected" or
    "fallback_unavailable". Never raises, always returns a sendable message.
    """
    amt = f"{round(event.amount):,}"
    template = FALLBACK_COPY.get(cause, FALLBACK_COPY[RootCause.TRANSIENT_RAIL])
    fallback = template.format(amt=amt)

    if not llm.enabled:
        ok, cleaned = validate_copy(fallback, event.amount)
        return (cleaned if ok else fallback), "fallback_unavailable", "no model configured"

    user = json.dumps({
        "root_cause": cause.value,
        "what_happened": PLAYBOOKS[cause].explanation,
        "amount_rupees": round(event.amount),
        "method_used": event.method,
        "is_subscription": event.is_subscription,
    }, indent=2)

    try:
        obj = _parse_json(llm.complete(COPY_SYSTEM, user))
        candidate = str(obj.get("message", ""))
    except (LLMUnavailable, ValueError, json.JSONDecodeError) as e:
        return fallback, "fallback_unavailable", f"model call failed: {e}"

    ok, result = validate_copy(candidate, event.amount)
    if not ok:
        return fallback, "fallback_rejected", f"copy rejected: {result}"
    return result, "llm", "passed copy validation"


# ------------------------------------------------- action proposal + cage

def validate_proposed_action(proposed: str, cause: RootCause) -> tuple:
    """
    The model may suggest an action. It may only suggest one that already exists
    in that cause's playbook - the gates then run on it exactly as they would on
    a rule-chosen action. This function is the only door in.
    """
    name = str(proposed or "").strip().upper()
    if name not in Action.__members__:
        return None, f"unrecognised action '{name}'"
    action = Action[name]
    if action not in PLAYBOOKS[cause].candidates:
        return None, (f"{action.value} is not in the {cause.value} playbook "
                      f"({[a.value for a in PLAYBOOKS[cause].candidates]})")
    return action, "in playbook; still subject to all gates"
