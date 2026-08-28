"""
The policy engine. Two layers, in this order, always:

  1. HARD GATES  - deterministic, non-negotiable, evaluated before anything else.
                   No expected-value calculation and no language model can open
                   a closed gate. Every evaluation is logged whether it passes
                   or fails, so the audit trail shows what we chose *not* to do.

  2. EV SCORING  - among actions that survive the gates, pick the one with the
                   highest expected value, where value is net rupees after the
                   cost of the action and the goodwill it spends.

This ordering is the design. An agent that can talk itself past a compliance
rule is not an agent, it is a liability.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from .taxonomy import (
    Action, RootCause, PLAYBOOKS, ACTION_COST, ANNOYANCE_COST, CONFIDENCE_FLOOR,
)


# ---------------------------------------------------------------- configuration

@dataclass
class Guardrails:
    quiet_hours_start: int = 21          # IST. No outbound messaging 21:00 -> 09:00
    quiet_hours_end: int = 9
    max_attempts_per_event: int = 3
    max_touches_per_customer_7d: int = 2  # outbound messages only
    cooldown_hours: int = 6
    min_amount_rupees: float = 20.0       # below this, recovery costs more than it earns
    ev_threshold_rupees: float = 1.50     # act only if EV clears this
    daily_outreach_budget_rupees: float = 2500.0
    voice_call_min_amount_rupees: float = 4000.0  # never cold-call over small money
    kill_switch: bool = False


OUTREACH_ACTIONS = {
    Action.WHATSAPP_NUDGE, Action.SMS_NUDGE,
    Action.VOICE_CALL, Action.SEND_PAYMENT_LINK,
}


# Belief calibration. The engine is deliberately a little optimistic relative to
# the true world - real agents overestimate their own interventions, and we want
# the measured result to reflect decisions taken under that bias, not under
# omniscience.
# The agent's global haircut on its own efficacy estimates. The generator's
# true scale is 0.42, so this agent is systematically ~19% OPTIMISTIC about
# every action it considers, and every expected value it computes is inflated
# by roughly that much.
#
# That gap is deliberate and left in. A deployed agent never knows the true
# scale, and a system whose guardrails only hold when its priors are right is
# not a system worth deploying. `stress.py` tests a far worse case: beliefs
# fully inverted. Zero forbidden retries in every world.
BELIEF_SCALE = 0.50

# The engine's *believed* success rates. Deliberately not equal to the true rates
# in generator.py - a live agent works from priors, not from omniscience. Every
# measured result in this project is therefore a decision made under uncertainty.
BELIEVED_EFFICACY = {
    RootCause.TRANSIENT_RAIL: {
        Action.RETRY_NOW: 0.30, Action.RETRY_SCHEDULED: 0.52,
        Action.SWITCH_RAIL_UPI: 0.55, Action.SEND_PAYMENT_LINK: 0.38,
        Action.WHATSAPP_NUDGE: 0.27, Action.SMS_NUDGE: 0.17, Action.VOICE_CALL: 0.33,
    },
    RootCause.INSUFFICIENT_FUNDS: {
        Action.RETRY_NOW: 0.08, Action.RETRY_SCHEDULED: 0.40,
        Action.SWITCH_RAIL_UPI: 0.14, Action.SEND_PAYMENT_LINK: 0.26,
        Action.WHATSAPP_NUDGE: 0.30, Action.SMS_NUDGE: 0.15, Action.VOICE_CALL: 0.34,
    },
    RootCause.AUTH_FRICTION: {
        Action.RETRY_NOW: 0.24, Action.RETRY_SCHEDULED: 0.28,
        Action.SWITCH_RAIL_UPI: 0.60, Action.SEND_PAYMENT_LINK: 0.50,
        Action.WHATSAPP_NUDGE: 0.40, Action.SMS_NUDGE: 0.22, Action.VOICE_CALL: 0.43,
    },
    RootCause.INSTRUMENT_DEAD: {
        Action.RETRY_NOW: 0.01, Action.RETRY_SCHEDULED: 0.01,
        Action.SWITCH_RAIL_UPI: 0.20, Action.SEND_PAYMENT_LINK: 0.34,
        Action.WHATSAPP_NUDGE: 0.28, Action.SMS_NUDGE: 0.13, Action.VOICE_CALL: 0.36,
    },
    RootCause.RISK_BLOCKED: {a: 0.0 for a in Action},
    RootCause.CUSTOMER_ABANDON: {
        Action.RETRY_NOW: 0.03, Action.RETRY_SCHEDULED: 0.04,
        Action.SWITCH_RAIL_UPI: 0.14, Action.SEND_PAYMENT_LINK: 0.24,
        Action.WHATSAPP_NUDGE: 0.31, Action.SMS_NUDGE: 0.14, Action.VOICE_CALL: 0.26,
    },
    RootCause.MANDATE_FAILURE: {
        Action.RETRY_NOW: 0.11, Action.RETRY_SCHEDULED: 0.37,
        Action.SWITCH_RAIL_UPI: 0.17, Action.SEND_PAYMENT_LINK: 0.30,
        Action.WHATSAPP_NUDGE: 0.25, Action.SMS_NUDGE: 0.12, Action.VOICE_CALL: 0.32,
    },
    RootCause.UNKNOWN: {a: 0.08 for a in Action},
}


# ------------------------------------------------------------------ data types

@dataclass
class GateResult:
    gate: str
    passed: bool
    detail: str


@dataclass
class Decision:
    event_id: str
    cause: RootCause
    confidence: float
    rationale: str
    action: Action
    scheduled_for: datetime
    expected_value: float
    gates: list = field(default_factory=list)
    blocked_actions: dict = field(default_factory=dict)  # Action -> blocking gate
    # Every candidate's full gate run, in playbook order. This is what lets the
    # audit answer "what did you want to do, and what stopped you?" rather than
    # only "what did you end up doing?".
    gate_runs: list = field(default_factory=list)
    notes: str = ""

    @property
    def is_action(self) -> bool:
        return self.action not in (Action.NO_ACTION, Action.ESCALATE_HUMAN)


@dataclass
class CustomerState:
    touches_7d: list = field(default_factory=list)   # datetimes of outbound msgs
    last_touch: datetime = None
    opted_out: bool = False
    hard_declined: bool = False                      # said "stop" / disputed


# ------------------------------------------------------------------ the engine

class PolicyEngine:
    def __init__(self, guardrails: Guardrails = None):
        self.g = guardrails or Guardrails()
        self.customers = {}
        self.spend_by_day = {}
        self.gate_block_counts = {}

    def _cust(self, cid) -> CustomerState:
        return self.customers.setdefault(cid, CustomerState())

    # ---- hard gates -------------------------------------------------------

    def evaluate_gates(self, event, cause, confidence, action, when, attempt_no):
        """Run every gate. Returns (allowed: bool, results: list[GateResult])."""
        g = self.g
        cs = self._cust(event.customer_id)
        pb = PLAYBOOKS[cause]
        out = []

        def gate(name, passed, detail):
            out.append(GateResult(name, passed, detail))

        gate("kill_switch", not g.kill_switch,
             "global kill switch engaged" if g.kill_switch else "disengaged")

        gate("confidence_floor", confidence >= CONFIDENCE_FLOOR,
             f"classification confidence {confidence:.2f} vs floor {CONFIDENCE_FLOOR:.2f}")

        gate("risk_decline_is_final", cause != RootCause.RISK_BLOCKED,
             "risk/compliance declines are never worked around"
             if cause == RootCause.RISK_BLOCKED else "not a risk decline")

        retryish = action in (Action.RETRY_NOW, Action.RETRY_SCHEDULED)
        gate("retry_permitted_for_cause", not (retryish and pb.retry_forbidden),
             f"{cause.value} forbids retrying the same instrument"
             if (retryish and pb.retry_forbidden) else "retry permitted or not a retry")

        gate("attempt_cap", attempt_no <= min(g.max_attempts_per_event, pb.max_attempts),
             f"attempt {attempt_no} vs cap {min(g.max_attempts_per_event, pb.max_attempts)}")

        gate("customer_opt_out", not (cs.opted_out or event.opted_out),
             "customer has opted out of recovery contact"
             if (cs.opted_out or event.opted_out) else "no opt-out on record")

        gate("hard_decline_respected", not cs.hard_declined,
             "customer explicitly asked us to stop" if cs.hard_declined else "clean")

        gate("min_ticket_size", event.amount >= g.min_amount_rupees,
             f"Rs {event.amount:.2f} vs floor Rs {g.min_amount_rupees:.2f}")

        is_outreach = action in OUTREACH_ACTIONS
        if is_outreach:
            in_quiet = when.hour >= g.quiet_hours_start or when.hour < g.quiet_hours_end
            gate("quiet_hours", not in_quiet,
                 f"{when:%H:%M} IST falls inside {g.quiet_hours_start:02d}:00-"
                 f"{g.quiet_hours_end:02d}:00 quiet window" if in_quiet
                 else f"{when:%H:%M} IST is inside contact hours")

            window = when - timedelta(days=7)
            recent = [t for t in cs.touches_7d if t >= window]
            gate("touch_frequency_cap", len(recent) < g.max_touches_per_customer_7d,
                 f"{len(recent)} outbound touches in trailing 7d vs cap "
                 f"{g.max_touches_per_customer_7d}")

            if cs.last_touch:
                delta_h = (when - cs.last_touch).total_seconds() / 3600
                gate("cooldown", delta_h >= g.cooldown_hours,
                     f"{delta_h:.1f}h since last touch vs {g.cooldown_hours}h cooldown")
            else:
                gate("cooldown", True, "no prior touch")

            if action == Action.WHATSAPP_NUDGE:
                gate("channel_reachable", event.contactable_whatsapp,
                     "no consented WhatsApp channel" if not event.contactable_whatsapp
                     else "WhatsApp consent on file")
            elif action == Action.SMS_NUDGE:
                gate("channel_reachable", event.contactable_sms,
                     "no SMS channel" if not event.contactable_sms else "SMS available")

            day = when.date()
            spent = self.spend_by_day.get(day, 0.0)
            cost = ACTION_COST[action]
            gate("daily_budget", spent + cost <= g.daily_outreach_budget_rupees,
                 f"Rs {spent:.2f} spent + Rs {cost:.2f} vs daily cap "
                 f"Rs {g.daily_outreach_budget_rupees:.2f}")

        if action == Action.VOICE_CALL:
            gate("voice_ticket_floor", event.amount >= g.voice_call_min_amount_rupees,
                 f"Rs {event.amount:.2f} vs voice floor "
                 f"Rs {g.voice_call_min_amount_rupees:.2f}")

        allowed = all(r.passed for r in out)
        if not allowed:
            for r in out:
                if not r.passed:
                    self.gate_block_counts[r.gate] = self.gate_block_counts.get(r.gate, 0) + 1
        return allowed, out

    # ---- expected value ---------------------------------------------------

    def expected_value(self, event, cause, action) -> float:
        p = BELIEVED_EFFICACY.get(cause, {}).get(action, 0.0) * BELIEF_SCALE
        # Decay belief for customers with a history of failures - they are
        # measurably harder to recover, and pretending otherwise wastes budget.
        p *= max(0.55, 1.0 - 0.09 * event.customer_prior_failures)
        gross = p * event.amount
        return gross - ACTION_COST[action] - ANNOYANCE_COST[action]

    # ---- the decision -----------------------------------------------------

    def decide(self, event, cause, confidence, rationale, now, attempt_no=1) -> Decision:
        pb = PLAYBOOKS[cause]
        when = now + timedelta(hours=pb.first_delay_hours if attempt_no == 1
                               else pb.first_delay_hours + 24 * (attempt_no - 1))
        # If the scheduled moment lands in quiet hours, an outreach action gets
        # deferred to 09:15 rather than dropped. Recovery is not urgent enough to
        # justify a 2am WhatsApp, and it is not worthless enough to abandon.
        deferred = False
        if when.hour >= self.g.quiet_hours_start or when.hour < self.g.quiet_hours_end:
            nxt = when.replace(hour=9, minute=15, second=0, microsecond=0)
            if when.hour >= self.g.quiet_hours_start:
                nxt += timedelta(days=1)
            when_outreach, deferred = nxt, True
        else:
            when_outreach = when

        blocked = {}
        scored = []
        runs = []

        for action in pb.candidates:
            at = when_outreach if action in OUTREACH_ACTIONS else when
            allowed, gates = self.evaluate_gates(event, cause, confidence,
                                                 action, at, attempt_no)
            runs.append({"action": action.value, "allowed": allowed,
                         "gates": [asdict(g) for g in gates]})
            if not allowed:
                blocked[action.value] = [g.gate for g in gates if not g.passed]
                continue
            ev = self.expected_value(event, cause, action)
            scored.append((ev, action, at, gates))

        if not scored:
            esc = cause in (RootCause.RISK_BLOCKED, RootCause.UNKNOWN, RootCause.MANDATE_FAILURE)
            return Decision(
                event_id=event.event_id, cause=cause, confidence=confidence,
                rationale=rationale,
                action=Action.ESCALATE_HUMAN if esc else Action.NO_ACTION,
                scheduled_for=now, expected_value=0.0, gates=[],
                blocked_actions=blocked, gate_runs=runs,
                notes="every candidate action was blocked by a hard gate",
            )

        scored.sort(key=lambda t: t[0], reverse=True)
        ev, action, at, gates = scored[0]

        if ev < self.g.ev_threshold_rupees:
            return Decision(
                event_id=event.event_id, cause=cause, confidence=confidence,
                rationale=rationale, action=Action.NO_ACTION, scheduled_for=now,
                expected_value=ev, gates=gates, blocked_actions=blocked,
                gate_runs=runs,
                notes=(f"best available action {action.value} scored Rs {ev:.2f}, "
                       f"below the Rs {self.g.ev_threshold_rupees:.2f} threshold - "
                       "chasing this costs more than it returns"),
            )

        return Decision(
            event_id=event.event_id, cause=cause, confidence=confidence,
            rationale=rationale, action=action, scheduled_for=at,
            expected_value=ev, gates=gates, blocked_actions=blocked,
            gate_runs=runs,
            notes="deferred out of quiet hours" if (deferred and action in OUTREACH_ACTIONS) else "",
        )

    # ---- bookkeeping ------------------------------------------------------

    def commit(self, event, decision):
        """Record the side effects of an action we actually fired."""
        if decision.action in OUTREACH_ACTIONS:
            cs = self._cust(event.customer_id)
            cs.touches_7d.append(decision.scheduled_for)
            cs.last_touch = decision.scheduled_for
            day = decision.scheduled_for.date()
            self.spend_by_day[day] = self.spend_by_day.get(day, 0.0) + ACTION_COST[decision.action]
