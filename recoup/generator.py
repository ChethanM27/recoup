"""
Synthetic cohort generator for at-risk revenue events.

Every event carries a *latent* ground truth: how likely this specific customer is
to pay, under each possible intervention, and whether they would have paid with no
intervention at all (organic recovery). The engine never sees these fields - it
only sees what a Razorpay webhook would actually carry. Outcomes are drawn from
the latent truth when an action fires.

That separation is the whole point. It is what makes "money recovered" a measured
number rather than a claimed one, and it is what makes the holdout control group
mean something.
"""

import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from .taxonomy import RootCause, Action, REASON_MAP


# Realistic-ish mix of failure reasons on an Indian mid-market merchant's book.
# Weights reflect the shape of the problem: rail wobble and auth friction dominate
# volume, risk blocks are rare but expensive to mishandle.
REASON_WEIGHTS = [
    ("gateway_technical_error", 90), ("issuer_down", 45), ("payment_timeout", 60),
    ("upi_collect_expired", 70), ("server_error", 18),
    ("insufficient_funds", 105), ("payment_limit_exceeded", 22),
    ("incorrect_otp", 88), ("authentication_failed", 46),
    ("otp_not_received", 40), ("3ds_timeout", 34),
    ("card_expired", 38), ("card_blocked", 16),
    ("invalid_vpa", 30), ("account_closed", 8), ("invalid_card_number", 12),
    ("risk_threshold_exceeded", 20), ("international_transaction_not_allowed", 9),
    ("suspected_fraud", 6),
    ("payment_cancelled", 76), ("checkout_abandoned", 92), ("user_dropped", 44),
    ("mandate_not_active", 26), ("mandate_revoked", 10), ("debit_not_presented", 30),
    # Deliberate long tail of reasons absent from REASON_MAP, so the exception
    # path and the confidence floor get genuinely exercised.
    ("bank_reference_missing", 14), ("acquirer_unmapped_code", 11),
    ("psp_unavailable_x9", 8),
]

SOURCE_BY_REASON = {
    "gateway_technical_error": ("gateway", "payment_authorization"),
    "issuer_down": ("bank", "payment_authorization"),
    "payment_timeout": ("gateway", "payment_initiation"),
    "server_error": ("gateway", "payment_initiation"),
    "upi_collect_expired": ("bank", "payment_authorization"),
    "insufficient_funds": ("bank", "payment_authorization"),
    "payment_limit_exceeded": ("bank", "payment_authorization"),
    "incorrect_otp": ("customer", "payment_authentication"),
    "authentication_failed": ("customer", "payment_authentication"),
    "otp_not_received": ("bank", "payment_authentication"),
    "3ds_timeout": ("customer", "payment_authentication"),
    "card_expired": ("customer", "payment_initiation"),
    "card_blocked": ("bank", "payment_authorization"),
    "invalid_vpa": ("customer", "payment_initiation"),
    "account_closed": ("bank", "payment_authorization"),
    "invalid_card_number": ("customer", "payment_initiation"),
    "risk_threshold_exceeded": ("business", "payment_authorization"),
    "international_transaction_not_allowed": ("business", "payment_authorization"),
    "suspected_fraud": ("business", "payment_authorization"),
    "payment_cancelled": ("customer", "payment_initiation"),
    "checkout_abandoned": ("customer", "payment_initiation"),
    "user_dropped": ("customer", "payment_authentication"),
    "mandate_not_active": ("bank", "payment_authorization"),
    "mandate_revoked": ("bank", "payment_authorization"),
    "debit_not_presented": ("bank", "payment_initiation"),
    "bank_reference_missing": ("bank", "payment_authorization"),
    "acquirer_unmapped_code": ("gateway", "payment_authorization"),
    "psp_unavailable_x9": ("gateway", "payment_initiation"),
}

METHOD_BY_REASON_DEFAULT = ["upi", "card", "netbanking", "wallet"]


# Calibration. Published Indian payments benchmarks put realistic incremental
# recovery on failed transactions in the low-to-mid teens of percentage points,
# not the 50pp a naive simulator will happily hand you. This scalar pulls the
# simulated world down to that range so the headline number survives scrutiny.
EFFICACY_SCALE = 0.42

# Latent true efficacy: P(customer pays | action taken), by true root cause.
# These are the physics of the simulated world. The engine has its own separate
# *beliefs* (priors in policy.py) which are deliberately not identical - a real
# agent never knows the true rates, and we want the measured result to reflect
# decisions made under uncertainty.
TRUE_EFFICACY = {
    RootCause.TRANSIENT_RAIL: {
        Action.RETRY_NOW: 0.34, Action.RETRY_SCHEDULED: 0.58,
        Action.SWITCH_RAIL_UPI: 0.62, Action.SEND_PAYMENT_LINK: 0.41,
        Action.WHATSAPP_NUDGE: 0.30, Action.SMS_NUDGE: 0.19, Action.VOICE_CALL: 0.35,
    },
    RootCause.INSUFFICIENT_FUNDS: {
        Action.RETRY_NOW: 0.06, Action.RETRY_SCHEDULED: 0.44,
        Action.SWITCH_RAIL_UPI: 0.11, Action.SEND_PAYMENT_LINK: 0.29,
        Action.WHATSAPP_NUDGE: 0.33, Action.SMS_NUDGE: 0.16, Action.VOICE_CALL: 0.38,
    },
    RootCause.AUTH_FRICTION: {
        Action.RETRY_NOW: 0.21, Action.RETRY_SCHEDULED: 0.26,
        Action.SWITCH_RAIL_UPI: 0.67, Action.SEND_PAYMENT_LINK: 0.55,
        Action.WHATSAPP_NUDGE: 0.44, Action.SMS_NUDGE: 0.24, Action.VOICE_CALL: 0.47,
    },
    RootCause.INSTRUMENT_DEAD: {
        Action.RETRY_NOW: 0.0, Action.RETRY_SCHEDULED: 0.0,
        Action.SWITCH_RAIL_UPI: 0.18, Action.SEND_PAYMENT_LINK: 0.37,
        Action.WHATSAPP_NUDGE: 0.31, Action.SMS_NUDGE: 0.14, Action.VOICE_CALL: 0.40,
    },
    RootCause.RISK_BLOCKED: {
        Action.RETRY_NOW: 0.0, Action.RETRY_SCHEDULED: 0.0,
        Action.SWITCH_RAIL_UPI: 0.0, Action.SEND_PAYMENT_LINK: 0.0,
        Action.WHATSAPP_NUDGE: 0.0, Action.SMS_NUDGE: 0.0, Action.VOICE_CALL: 0.0,
    },
    RootCause.CUSTOMER_ABANDON: {
        Action.RETRY_NOW: 0.02, Action.RETRY_SCHEDULED: 0.03,
        Action.SWITCH_RAIL_UPI: 0.12, Action.SEND_PAYMENT_LINK: 0.26,
        Action.WHATSAPP_NUDGE: 0.34, Action.SMS_NUDGE: 0.15, Action.VOICE_CALL: 0.29,
    },
    RootCause.MANDATE_FAILURE: {
        Action.RETRY_NOW: 0.09, Action.RETRY_SCHEDULED: 0.41,
        Action.SWITCH_RAIL_UPI: 0.15, Action.SEND_PAYMENT_LINK: 0.33,
        Action.WHATSAPP_NUDGE: 0.28, Action.SMS_NUDGE: 0.13, Action.VOICE_CALL: 0.36,
    },
    RootCause.UNKNOWN: {a: 0.10 for a in Action},
}

# P(customer pays on their own, no intervention) within the observation window.
# This is the number the control group measures, and the number every naive
# recovery dashboard silently claims credit for.
TRUE_ORGANIC = {
    RootCause.TRANSIENT_RAIL: 0.29,
    RootCause.INSUFFICIENT_FUNDS: 0.17,
    RootCause.AUTH_FRICTION: 0.24,
    RootCause.INSTRUMENT_DEAD: 0.07,
    RootCause.RISK_BLOCKED: 0.0,
    RootCause.CUSTOMER_ABANDON: 0.11,
    RootCause.MANDATE_FAILURE: 0.14,
    RootCause.UNKNOWN: 0.12,
}


@dataclass
class Event:
    """What the engine is allowed to see - the shape of a real webhook payload."""
    event_id: str
    customer_id: str
    order_id: str
    amount_paise: int
    currency: str
    method: str
    failed_at: datetime
    error_source: str
    error_step: str
    error_reason: str
    error_code: str
    attempt_no: int
    customer_ltv_paise: int
    customer_prior_failures: int
    contactable_whatsapp: bool
    contactable_sms: bool
    opted_out: bool
    is_subscription: bool

    # ---- latent ground truth, never read by the decision engine ----
    _true_cause: RootCause = field(default=RootCause.UNKNOWN, repr=False)
    _label_noise: str = field(default="clean", repr=False)
    _true_efficacy: dict = field(default_factory=dict, repr=False)
    _true_organic: float = field(default=0.0, repr=False)
    # Customer-level responsiveness multiplier: some people just don't respond.
    _responsiveness: float = field(default=1.0, repr=False)

    @property
    def amount(self) -> float:
        return self.amount_paise / 100.0

    def observable(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if not k.startswith("_")}


def _weighted_choice(rng, pairs):
    total = sum(w for _, w in pairs)
    r = rng.uniform(0, total)
    acc = 0.0
    for item, w in pairs:
        acc += w
        if r <= acc:
            return item
    return pairs[-1][0]


def _amount_paise(rng) -> int:
    """Long-tailed order values: lots of small, a few large. Median ~ Rs 900."""
    v = rng.lognormvariate(6.85, 1.05)  # in rupees
    return int(max(49.0, min(v, 250000.0)) * 100)


# ---------------------------------------------------------------- label noise
#
# Real gateways do not always return a code that matches what actually happened.
# An issuer that declines a dead card often returns a generic failure; a risk
# decline is frequently masked behind an ordinary-looking auth error. Without
# this, `_true_cause` is derived from the same REASON_MAP the classifier reads,
# so any accuracy figure would be circular and meaningless.
#
# Two noise modes, with very different consequences:
#   degraded - the code goes generic. The classifier SHOULD abstain and route
#              to a human. Failing loudly is the acceptable outcome.
#   confused - the code is wrong but valid. The classifier will be CONFIDENTLY
#              WRONG. This is the dangerous mode and the one worth measuring.
LABEL_NOISE = 0.14
DEGRADE_SHARE = 0.5

_GENERIC = ["bank_reference_missing", "acquirer_unmapped_code",
            "psp_unavailable_x9", "unspecified_decline"]

# true cause -> codes a real gateway might emit instead
_CONFUSABLE = {
    RootCause.INSUFFICIENT_FUNDS: ["payment_timeout", "gateway_technical_error"],
    RootCause.INSTRUMENT_DEAD:    ["insufficient_funds", "authentication_failed"],
    RootCause.AUTH_FRICTION:      ["payment_timeout", "gateway_technical_error"],
    RootCause.RISK_BLOCKED:       ["authentication_failed", "gateway_technical_error"],
    RootCause.MANDATE_FAILURE:    ["insufficient_funds", "payment_timeout"],
    RootCause.CUSTOMER_ABANDON:   ["payment_timeout"],
    RootCause.TRANSIENT_RAIL:     ["authentication_failed"],
}

_GENERIC_SRC = {
    "bank_reference_missing":  ("bank", "payment_authorization"),
    "acquirer_unmapped_code":  ("gateway", "payment_authorization"),
    "psp_unavailable_x9":      ("gateway", "payment_initiation"),
    "unspecified_decline":     ("bank", "payment_authorization"),
}


def _apply_label_noise(rng, true_cause, reason, source, step):
    """Return the (possibly corrupted) code the gateway actually reports."""
    if rng.random() >= LABEL_NOISE:
        return reason, source, step, "clean"

    if rng.random() < DEGRADE_SHARE:
        obs = rng.choice(_GENERIC)
        src, stp = _GENERIC_SRC[obs]
        return obs, src, stp, "degraded"

    opts = _CONFUSABLE.get(true_cause)
    if not opts:
        return reason, source, step, "clean"
    obs = rng.choice(opts)
    src, stp = SOURCE_BY_REASON.get(obs, (source, step))
    return obs, src, stp, "confused"


def generate_cohort(n: int = 600, seed: int = 7, days: int = 30,
                    label_noise: bool = True) -> list:
    """Generate n at-risk events spread over the trailing `days` window."""
    rng = random.Random(seed)
    now = datetime(2026, 8, 21, 10, 0, 0)
    start = now - timedelta(days=days)

    events = []
    for i in range(n):
        reason = _weighted_choice(rng, REASON_WEIGHTS)
        source, step = SOURCE_BY_REASON[reason]
        true_cause = REASON_MAP.get(reason)

        if true_cause is None:
            # Long-tail reason. It still has a real underlying cause in the world;
            # the engine simply has no rule for it. This is what an honest
            # exception list is supposed to catch.
            true_cause = _weighted_choice(rng, [
                (RootCause.TRANSIENT_RAIL, 5), (RootCause.AUTH_FRICTION, 3),
                (RootCause.INSUFFICIENT_FUNDS, 2), (RootCause.INSTRUMENT_DEAD, 1),
            ])

        if true_cause == RootCause.INSTRUMENT_DEAD and "vpa" in reason:
            method = "upi"
        elif true_cause == RootCause.INSTRUMENT_DEAD:
            method = "card"
        elif true_cause == RootCause.MANDATE_FAILURE:
            method = rng.choice(["emandate", "upi_autopay"])
        else:
            method = _weighted_choice(rng, [("upi", 58), ("card", 26),
                                            ("netbanking", 10), ("wallet", 6)])

        # The gateway's reported code may not match reality.
        if label_noise:
            reason, source, step, noise_kind = _apply_label_noise(
                rng, true_cause, reason, source, step)
        else:
            noise_kind = "clean"

        # Responsiveness: beta-ish spread so cohorts aren't homogeneous.
        responsiveness = max(0.15, min(1.85, rng.gauss(1.0, 0.34)))

        efficacy = {
            a: max(0.0, min(0.97, p * responsiveness * EFFICACY_SCALE))
            for a, p in TRUE_EFFICACY[true_cause].items()
        }
        efficacy[Action.ESCALATE_HUMAN] = 0.0
        efficacy[Action.NO_ACTION] = 0.0

        offset = timedelta(seconds=rng.uniform(0, days * 86400))
        failed_at = start + offset

        opted_out = rng.random() < 0.06
        events.append(Event(
            event_id=f"evt_{i:05d}",
            customer_id=f"cust_{rng.randint(1, max(2, int(n * 0.72))):05d}",
            order_id=f"order_{rng.randrange(10**11, 10**12)}",
            amount_paise=_amount_paise(rng),
            currency="INR",
            method=method,
            failed_at=failed_at,
            error_source=source,
            error_step=step,
            error_reason=reason,
            error_code="BAD_REQUEST_ERROR" if source == "customer" else "GATEWAY_ERROR",
            attempt_no=1 if rng.random() < 0.82 else 2,
            customer_ltv_paise=int(_amount_paise(rng) * rng.uniform(1.0, 14.0)),
            customer_prior_failures=max(0, int(rng.gauss(0.8, 1.2))),
            contactable_whatsapp=(not opted_out) and rng.random() < 0.86,
            contactable_sms=(not opted_out) and rng.random() < 0.95,
            opted_out=opted_out,
            is_subscription=true_cause == RootCause.MANDATE_FAILURE,
            _true_cause=true_cause,
            _label_noise=noise_kind,
            _true_efficacy=efficacy,
            _true_organic=max(0.0, min(0.9, TRUE_ORGANIC[true_cause] * responsiveness)),
            _responsiveness=responsiveness,
        ))

    events.sort(key=lambda e: e.failed_at)
    return events
