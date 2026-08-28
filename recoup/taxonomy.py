"""
Root-cause taxonomy for at-risk revenue events.

Maps Razorpay-style failure signatures (error_source / error_step / error_reason)
onto a small set of root causes. Each root cause has a distinct playbook, because
an expired card and a timed-out bank gateway are not the same problem and must
never receive the same treatment.
"""

from dataclasses import dataclass, field
from enum import Enum


class RootCause(str, Enum):
    TRANSIENT_RAIL = "TRANSIENT_RAIL"          # gateway/issuer wobble, no customer fault
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"  # money will exist later
    AUTH_FRICTION = "AUTH_FRICTION"            # OTP/3DS drop-off, customer willing but blocked
    INSTRUMENT_DEAD = "INSTRUMENT_DEAD"        # card expired/blocked, VPA invalid
    RISK_BLOCKED = "RISK_BLOCKED"              # declined by risk/compliance - NEVER auto-retry
    CUSTOMER_ABANDON = "CUSTOMER_ABANDON"      # cancelled, walked away, still interested
    MANDATE_FAILURE = "MANDATE_FAILURE"        # subscription / e-mandate presentation failed
    UNKNOWN = "UNKNOWN"                        # unclassified -> exception list, human review


class Action(str, Enum):
    RETRY_NOW = "RETRY_NOW"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SWITCH_RAIL_UPI = "SWITCH_RAIL_UPI"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    WHATSAPP_NUDGE = "WHATSAPP_NUDGE"
    SMS_NUDGE = "SMS_NUDGE"
    VOICE_CALL = "VOICE_CALL"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    NO_ACTION = "NO_ACTION"


# Unit economics of each intervention, in INR. Outreach costs are real per-message
# rates; a retry costs a fraction of a paisa but is never free because a failed
# retry burns issuer trust and can raise the merchant's decline rate.
ACTION_COST = {
    Action.RETRY_NOW: 0.05,
    Action.RETRY_SCHEDULED: 0.05,
    Action.SWITCH_RAIL_UPI: 0.10,
    Action.SEND_PAYMENT_LINK: 0.35,
    Action.WHATSAPP_NUDGE: 0.80,
    Action.SMS_NUDGE: 0.18,
    Action.VOICE_CALL: 2.60,
    Action.ESCALATE_HUMAN: 45.00,
    Action.NO_ACTION: 0.0,
}

# "Annoyance cost" - the goodwill we spend on the customer, priced in INR so it
# sits in the same expected-value equation as everything else. A silent retry
# costs nothing. A voice call costs real relationship capital.
ANNOYANCE_COST = {
    Action.RETRY_NOW: 0.0,
    Action.RETRY_SCHEDULED: 0.0,
    Action.SWITCH_RAIL_UPI: 0.5,
    Action.SEND_PAYMENT_LINK: 1.0,
    Action.WHATSAPP_NUDGE: 3.0,
    Action.SMS_NUDGE: 2.0,
    Action.VOICE_CALL: 12.0,
    Action.ESCALATE_HUMAN: 0.0,
    Action.NO_ACTION: 0.0,
}


@dataclass(frozen=True)
class Playbook:
    cause: RootCause
    human_label: str
    explanation: str
    # Ordered candidate actions. The policy engine filters and scores these;
    # order here encodes "cheapest, least invasive first".
    candidates: list = field(default_factory=list)
    # Hours to wait before the first intervention fires.
    first_delay_hours: float = 0.0
    max_attempts: int = 3
    # Hard block: some causes must never be auto-retried, regardless of EV.
    retry_forbidden: bool = False


PLAYBOOKS = {
    RootCause.TRANSIENT_RAIL: Playbook(
        cause=RootCause.TRANSIENT_RAIL,
        human_label="Rail wobble",
        explanation=(
            "Gateway or issuer failed on our side of the fence. The customer did "
            "nothing wrong and usually does not even know it failed. Retry quietly "
            "with backoff; switch rails if the same rail fails twice."
        ),
        candidates=[Action.RETRY_NOW, Action.RETRY_SCHEDULED, Action.SWITCH_RAIL_UPI],
        first_delay_hours=0.25,
        max_attempts=3,
    ),
    RootCause.INSUFFICIENT_FUNDS: Playbook(
        cause=RootCause.INSUFFICIENT_FUNDS,
        human_label="No funds yet",
        explanation=(
            "The account was short. Retrying in 20 minutes just burns another "
            "decline. Time the retry to when money lands - salary week, or the "
            "1st and 7th - and keep outreach soft."
        ),
        candidates=[Action.RETRY_SCHEDULED, Action.WHATSAPP_NUDGE, Action.SEND_PAYMENT_LINK],
        first_delay_hours=54.0,
        max_attempts=2,
    ),
    RootCause.AUTH_FRICTION: Playbook(
        cause=RootCause.AUTH_FRICTION,
        human_label="Blocked at authentication",
        explanation=(
            "The customer wanted to pay and got stuck at OTP or 3DS. Highest-intent "
            "cohort in the whole book. Do not retry the same high-friction path - "
            "hand them a lower-friction rail immediately."
        ),
        candidates=[Action.SWITCH_RAIL_UPI, Action.SEND_PAYMENT_LINK, Action.WHATSAPP_NUDGE],
        first_delay_hours=0.1,
        max_attempts=3,
    ),
    RootCause.INSTRUMENT_DEAD: Playbook(
        cause=RootCause.INSTRUMENT_DEAD,
        human_label="Instrument unusable",
        explanation=(
            "Card expired, card blocked, or VPA does not resolve. Retrying this "
            "instrument is guaranteed to fail - it is not a recovery strategy, it "
            "is spam. The only path is collecting a new instrument."
        ),
        candidates=[Action.SEND_PAYMENT_LINK, Action.WHATSAPP_NUDGE, Action.SMS_NUDGE],
        first_delay_hours=0.5,
        max_attempts=2,
        retry_forbidden=True,
    ),
    RootCause.RISK_BLOCKED: Playbook(
        cause=RootCause.RISK_BLOCKED,
        human_label="Declined by risk",
        explanation=(
            "Risk or compliance said no. An agent that retries around a risk "
            "decline is an agent laundering a decline. Hard stop, route to a human, "
            "log it. This gate is not overridable by expected value."
        ),
        candidates=[Action.ESCALATE_HUMAN],
        first_delay_hours=0.0,
        max_attempts=1,
        retry_forbidden=True,
    ),
    RootCause.CUSTOMER_ABANDON: Playbook(
        cause=RootCause.CUSTOMER_ABANDON,
        human_label="Walked away",
        explanation=(
            "Customer cancelled or abandoned checkout. Nothing technical is broken, "
            "so there is nothing to retry. This is a persuasion problem: one nudge "
            "carrying the cart back, then leave them alone."
        ),
        candidates=[Action.WHATSAPP_NUDGE, Action.SEND_PAYMENT_LINK, Action.SMS_NUDGE],
        first_delay_hours=2.0,
        max_attempts=2,
        retry_forbidden=True,
    ),
    RootCause.MANDATE_FAILURE: Playbook(
        cause=RootCause.MANDATE_FAILURE,
        human_label="Mandate presentation failed",
        explanation=(
            "A subscription or e-mandate debit did not go through. Re-presentation "
            "is governed by scheme rules, not by our impatience. Space attempts and "
            "escalate to a human before the mandate lapses."
        ),
        candidates=[Action.RETRY_SCHEDULED, Action.WHATSAPP_NUDGE, Action.ESCALATE_HUMAN],
        first_delay_hours=72.0,
        max_attempts=3,
    ),
    RootCause.UNKNOWN: Playbook(
        cause=RootCause.UNKNOWN,
        human_label="Unclassified",
        explanation=(
            "We could not confidently attribute this failure. Guessing here is how "
            "you damage customers at scale. It goes on the exception list for a "
            "human, and it stays in the denominator of every metric we report."
        ),
        candidates=[Action.ESCALATE_HUMAN],
        first_delay_hours=0.0,
        max_attempts=1,
        retry_forbidden=True,
    ),
}


# Razorpay error_reason -> RootCause. Keys mirror the reason strings Razorpay
# surfaces on a failed payment entity.
REASON_MAP = {
    "gateway_technical_error": RootCause.TRANSIENT_RAIL,
    "issuer_down": RootCause.TRANSIENT_RAIL,
    "payment_timeout": RootCause.TRANSIENT_RAIL,
    "server_error": RootCause.TRANSIENT_RAIL,
    "upi_collect_expired": RootCause.TRANSIENT_RAIL,

    "insufficient_funds": RootCause.INSUFFICIENT_FUNDS,
    "payment_limit_exceeded": RootCause.INSUFFICIENT_FUNDS,

    "incorrect_otp": RootCause.AUTH_FRICTION,
    "authentication_failed": RootCause.AUTH_FRICTION,
    "otp_not_received": RootCause.AUTH_FRICTION,
    "3ds_timeout": RootCause.AUTH_FRICTION,

    "card_expired": RootCause.INSTRUMENT_DEAD,
    "card_blocked": RootCause.INSTRUMENT_DEAD,
    "invalid_vpa": RootCause.INSTRUMENT_DEAD,
    "account_closed": RootCause.INSTRUMENT_DEAD,
    "invalid_card_number": RootCause.INSTRUMENT_DEAD,

    "risk_threshold_exceeded": RootCause.RISK_BLOCKED,
    "international_transaction_not_allowed": RootCause.RISK_BLOCKED,
    "suspected_fraud": RootCause.RISK_BLOCKED,

    "payment_cancelled": RootCause.CUSTOMER_ABANDON,
    "checkout_abandoned": RootCause.CUSTOMER_ABANDON,
    "user_dropped": RootCause.CUSTOMER_ABANDON,

    "mandate_not_active": RootCause.MANDATE_FAILURE,
    "mandate_revoked": RootCause.MANDATE_FAILURE,
    "debit_not_presented": RootCause.MANDATE_FAILURE,
}


def classify(event) -> tuple:
    """
    Deterministic classification of a failure event.

    Returns (RootCause, confidence, rationale). We return confidence explicitly
    because an honest exception list is worth more than a confident wrong label:
    anything below the acceptance threshold is routed to a human instead of
    being acted on.
    """
    reason = (event.error_reason or "").strip().lower()
    source = (event.error_source or "").strip().lower()
    step = (event.error_step or "").strip().lower()

    if reason in REASON_MAP:
        cause = REASON_MAP[reason]
        conf = 0.97
        notes = []

        # Cross-checks. The reported code can be valid and still be wrong -
        # issuers mask dead instruments and risk declines behind ordinary
        # errors. These do not re-label the event; they withdraw the certainty,
        # which is enough to push a doubtful case onto the exception list
        # instead of into an action.
        repeats = max(getattr(event, "customer_prior_failures", 0) or 0,
                      (getattr(event, "attempt_no", 1) or 1) - 1)
        if cause == RootCause.TRANSIENT_RAIL and repeats >= 2:
            conf = 0.52
            notes.append(
                f"but this has failed {repeats + 1}x - a fault that keeps "
                "recurring is not transient")

        if getattr(event, "is_subscription", False) and cause not in (
                RootCause.MANDATE_FAILURE, RootCause.RISK_BLOCKED):
            conf = min(conf, 0.50)
            notes.append(
                "but this is a subscription debit, where the reported code "
                "often masks a mandate problem")

        rationale = f"error_reason '{reason}' maps directly to {cause.value}"
        if notes:
            rationale += " " + "; ".join(notes)
        return cause, conf, rationale

    # Fallback: reason is unrecognised, so infer from source + step. Lower
    # confidence on purpose - this is inference, not a lookup.
    if source == "bank" and step in ("payment_authorization", "payment_initiation"):
        return RootCause.TRANSIENT_RAIL, 0.39, (
            f"unknown reason '{reason}', but source=bank at step={step} "
            "is characteristic of a rail-side failure - measured 39% accurate, "
            "below the floor, so this routes to a human"
        )
    if source == "customer" and step == "payment_authentication":
        return RootCause.AUTH_FRICTION, 0.38, (
            f"unknown reason '{reason}', but source=customer at authentication "
            "is characteristic of OTP/3DS drop-off - measured 38% accurate, "
            "below the floor, so this routes to a human"
        )
    if source == "gateway":
        return RootCause.TRANSIENT_RAIL, 0.33, (
            f"unknown reason '{reason}', source=gateway implies our side failed "
            "- measured 33% accurate, below the floor, so this routes to a human"
        )
    if source == "business":
        return RootCause.RISK_BLOCKED, 0.35, (
            f"unknown reason '{reason}', source=business implies a merchant-side "
            "or policy block - treated as non-retryable by default and routed "
            "to a human, since acting on a suspected risk block is never safe"
        )

    return RootCause.UNKNOWN, 0.0, (
        f"no rule matched (reason='{reason}', source='{source}', step='{step}')"
    )


# Below this, we do not act. We escalate.
CONFIDENCE_FLOOR = 0.55
