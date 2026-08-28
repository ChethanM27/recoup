"""
The plain-English layer.

Every internal identifier in this project has a human name. RISK_BLOCKED is
precise and it is also unreadable to anyone who did not write it. A merchant ops
lead reading the dashboard should never have to decode an enum.

This module is the single place that translation lives, so the engine can stay
strict and the interface can stay legible.
"""

from .taxonomy import RootCause, Action

# ------------------------------------------------------------- root causes

CAUSE_NAME = {
    RootCause.TRANSIENT_RAIL:     "The bank glitched",
    RootCause.INSUFFICIENT_FUNDS: "No money in the account",
    RootCause.AUTH_FRICTION:      "Stuck at the OTP screen",
    RootCause.INSTRUMENT_DEAD:    "Their card is dead",
    RootCause.RISK_BLOCKED:       "Risk said no",
    RootCause.CUSTOMER_ABANDON:   "They changed their mind",
    RootCause.MANDATE_FAILURE:    "Auto-pay didn't go through",
    RootCause.UNKNOWN:            "We couldn't tell",
}

CAUSE_ONELINE = {
    RootCause.TRANSIENT_RAIL:     "Nothing to do with the customer. They probably don't even know it failed.",
    RootCause.INSUFFICIENT_FUNDS: "The money wasn't there. It will be, later.",
    RootCause.AUTH_FRICTION:      "They wanted to pay and the OTP screen ate it. Highest intent we'll see all day.",
    RootCause.INSTRUMENT_DEAD:    "Expired, blocked, or the UPI ID doesn't exist. This card will never work again.",
    RootCause.RISK_BLOCKED:       "Risk or compliance declined this. That decision is not ours to reverse.",
    RootCause.CUSTOMER_ABANDON:   "Nothing broke. They just walked away.",
    RootCause.MANDATE_FAILURE:    "A subscription debit failed. Scheme rules govern when we can try again.",
    RootCause.UNKNOWN:            "We don't know, and guessing at scale is how you hurt people.",
}

# ----------------------------------------------------------------- actions

ACTION_NAME = {
    Action.RETRY_NOW:          "Try again now",
    Action.RETRY_SCHEDULED:    "Try again later",
    Action.SWITCH_RAIL_UPI:    "Offer UPI instead",
    Action.SEND_PAYMENT_LINK:  "Send a fresh link",
    Action.WHATSAPP_NUDGE:     "WhatsApp them",
    Action.SMS_NUDGE:          "Text them",
    Action.VOICE_CALL:         "Call them",
    Action.ESCALATE_HUMAN:     "Hand to a human",
    Action.NO_ACTION:          "Leave them alone",
}

ACTION_WHY = {
    Action.RETRY_NOW:         "Costs nothing, customer never sees it.",
    Action.RETRY_SCHEDULED:   "Same, but timed for when it can actually work.",
    Action.SWITCH_RAIL_UPI:   "Skips the step that broke.",
    Action.SEND_PAYMENT_LINK: "A fresh link, any payment method, expires in 3 days.",
    Action.WHATSAPP_NUDGE:    "A real message to a real person. Spend this carefully.",
    Action.SMS_NUDGE:         "Cheaper, colder, lower response.",
    Action.VOICE_CALL:        "Expensive and intrusive. Only for large amounts.",
    Action.ESCALATE_HUMAN:    "Some decisions shouldn't be automated.",
    Action.NO_ACTION:         "Chasing this costs more than it returns.",
}

# ------------------------------------------------------------------- gates

GATE_NAME = {
    "kill_switch":               "Emergency stop is off",
    "confidence_floor":          "We're sure enough about the cause",
    "risk_decline_is_final":     "Risk hasn't already said no",
    "retry_permitted_for_cause": "Retrying could actually work here",
    "attempt_cap":               "We haven't already tried too many times",
    "customer_opt_out":          "They haven't opted out",
    "hard_decline_respected":    "They haven't told us to stop",
    "min_ticket_size":           "Worth more than it costs to chase",
    "quiet_hours":               "It isn't the middle of the night",
    "touch_frequency_cap":       "We haven't messaged them too much lately",
    "cooldown":                  "Enough time since the last message",
    "channel_reachable":         "We have a way to reach them",
    "daily_budget":              "Inside today's outreach budget",
    "voice_ticket_floor":        "Large enough to justify a phone call",
}

# ---------------------------------------------------------------- outcomes

OUTCOME_NAME = {
    "recovered":          "Paid",
    "recovered_organic":  "Paid on their own",
    "attempt_failed":     "Didn't work",
    "no_action_taken":    "Deliberately left alone",
    "escalated_to_human": "Sent to a human",
    "window_expired":     "Ran out of time",
    "lost":               "Lost",
}

OUTCOME_TONE = {
    "recovered": "win", "recovered_organic": "neutral", "attempt_failed": "loss",
    "no_action_taken": "neutral", "escalated_to_human": "neutral",
    "window_expired": "loss", "lost": "loss",
}


def cause_name(v):
    try:
        return CAUSE_NAME[RootCause(v)]
    except (ValueError, KeyError):
        return str(v).replace("_", " ").title()


def cause_oneline(v):
    try:
        return CAUSE_ONELINE[RootCause(v)]
    except (ValueError, KeyError):
        return ""


def action_name(v):
    try:
        return ACTION_NAME[Action(v)]
    except (ValueError, KeyError):
        return str(v).replace("_", " ").title()


def action_why(v):
    try:
        return ACTION_WHY[Action(v)]
    except (ValueError, KeyError):
        return ""


def gate_name(v):
    return GATE_NAME.get(v, str(v).replace("_", " ").capitalize())


def outcome_name(v):
    return OUTCOME_NAME.get(v, str(v).replace("_", " ").capitalize())


def outcome_tone(v):
    return OUTCOME_TONE.get(v, "neutral")
