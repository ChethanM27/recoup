"""
The batch runner.

Splits the cohort into treatment and a randomised holdout control, works the
treatment arm through the policy engine, draws outcomes from latent ground truth,
and reports incremental recovery: what we recovered *beyond what would have
happened anyway*.

The control arm is the honesty mechanism. Without it, every recovery number in
this industry is inflated by customers who were always going to pay.
"""

import json
import random
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta

from .taxonomy import (Action, RootCause, PLAYBOOKS, ACTION_COST,
                       CONFIDENCE_FLOOR, classify)
from .policy import PolicyEngine, Guardrails, OUTREACH_ACTIONS
from .llm import LLM, diagnose, write_copy
from .razorpay_client import RazorpayClient

HOLDOUT_FRACTION = 0.20
OBSERVATION_WINDOW_DAYS = 14


# ------------------------------------------------------------------ audit log

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    arm TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    amount_rupees REAL NOT NULL,
    error_reason TEXT,
    cause TEXT,
    confidence REAL,
    rationale TEXT,
    action TEXT,
    scheduled_for TEXT,
    expected_value REAL,
    gates_json TEXT,
    blocked_json TEXT,
    notes TEXT,
    outcome TEXT,
    recovered_rupees REAL,
    cost_rupees REAL,
    diag_source TEXT,
    copy_text TEXT,
    copy_source TEXT,
    copy_note TEXT,
    gateway_status TEXT,
    payment_link TEXT,
    gate_runs_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_cause ON audit(cause);
"""


class AuditLog:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def write(self, row: dict):
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO audit ({cols}) VALUES ({marks})",
                          list(row.values()))

    def commit(self):
        self.conn.commit()

    def query(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ------------------------------------------------------------------- outcomes

def draw_outcome(rng, event, action) -> bool:
    """Draw a real outcome from the customer's latent responsiveness."""
    p = event._true_efficacy.get(action, 0.0)
    p *= max(0.5, 1.0 - 0.10 * event.customer_prior_failures)
    return rng.random() < p


def draw_organic(rng, event) -> bool:
    """Would this customer have paid with no intervention at all?"""
    return rng.random() < event._true_organic


# ---------------------------------------------------------------- the runner

def run_batch(events, guardrails: Guardrails = None, seed: int = 11,
              db_path: str = ":memory:", holdout: float = HOLDOUT_FRACTION,
              llm: "LLM" = None, rz: "RazorpayClient" = None):
    rng = random.Random(seed)
    engine = PolicyEngine(guardrails)
    audit = AuditLog(db_path)
    llm = llm or LLM()
    rz = rz or RazorpayClient()
    stats = {"llm_diagnoses": 0, "llm_copy": 0, "copy_rejected": 0,
             "links_created": 0, "links_queued": 0, "rule_classified": 0}

    results = {"treatment": [], "control": []}
    exceptions = []

    for event in events:
        arm = "control" if rng.random() < holdout else "treatment"

        cause, confidence, rationale = classify(event)
        diag_source = "rules"
        stats["rule_classified"] += 1

        # The model is consulted only where the rules gave up. Where a rule
        # fired, the rule wins outright and no model call is made.
        if cause == RootCause.UNKNOWN or confidence < CONFIDENCE_FLOOR:
            l_cause, l_conf, l_rat, l_src = diagnose(llm, event)
            if l_src == "llm" and l_conf > confidence:
                cause, confidence, rationale = l_cause, l_conf, l_rat
                diag_source = "llm"
                stats["llm_diagnoses"] += 1
                stats["rule_classified"] -= 1

        if arm == "control":
            # Touched by nothing. Observed only.
            recovered = draw_organic(rng, event)
            audit.write(dict(
                ts=datetime.now().isoformat(timespec="seconds"),
                event_id=event.event_id, customer_id=event.customer_id, arm=arm,
                attempt_no=0, amount_rupees=event.amount,
                error_reason=event.error_reason, cause=cause.value,
                confidence=confidence, rationale="holdout: observed, not worked",
                diag_source=diag_source, copy_text="", copy_source="",
                copy_note="", gateway_status="", payment_link="",
                action="NONE", scheduled_for="", expected_value=0.0,
                gates_json="[]", blocked_json="{}", gate_runs_json="[]",
                notes="randomised holdout control arm",
                outcome="recovered_organic" if recovered else "lost",
                recovered_rupees=event.amount if recovered else 0.0,
                cost_rupees=0.0,
            ))
            results["control"].append({
                "event": event, "cause": cause,
                "recovered": recovered,
                "recovered_rupees": event.amount if recovered else 0.0,
                "cost": 0.0, "attempts": 0,
            })
            continue

        # ---- treatment arm: work it, up to the cause's attempt cap ----
        now = event.failed_at
        pb = PLAYBOOKS[cause]
        attempts, spend, recovered = 0, 0.0, False
        final_outcome, escalated = "lost", False
        deadline = event.failed_at + timedelta(days=OBSERVATION_WINDOW_DAYS)

        for attempt_no in range(1, min(pb.max_attempts, 3) + 1):
            decision = engine.decide(event, cause, confidence, rationale,
                                     now, attempt_no)

            if decision.action == Action.ESCALATE_HUMAN:
                escalated = True
                final_outcome = "escalated_to_human"
            elif decision.action == Action.NO_ACTION:
                final_outcome = "no_action_taken"

            if decision.scheduled_for > deadline:
                final_outcome = "window_expired"
                _log(audit, event, arm, attempt_no, decision, final_outcome,
                     0.0, 0.0, {"diag_source": diag_source})
                break

            cost = ACTION_COST[decision.action]
            spend += cost
            attempts += 1 if decision.is_action else 0

            extras = {"diag_source": diag_source, "copy_text": "", "copy_source": "",
                      "copy_note": "", "gateway_status": "", "payment_link": ""}

            if decision.is_action:
                # A real recovery link, created against Razorpay test mode. If
                # the gateway is unreachable the intent is queued, not lost, and
                # the batch keeps moving.
                if decision.action == Action.SEND_PAYMENT_LINK:
                    status, res = rz.execute_or_queue(
                        f"payment_link:{event.event_id}",
                        rz.create_payment_link,
                        event.amount_paise,
                        f"Recovery for order {event.order_id}",
                        event.event_id,
                    )
                    extras["gateway_status"] = status
                    if status == "executed":
                        extras["payment_link"] = res.get("short_url", "")
                        stats["links_created"] += 1
                    else:
                        stats["links_queued"] += 1

                if decision.action in OUTREACH_ACTIONS:
                    msg, src, note = write_copy(llm, event, cause)
                    link = extras["payment_link"] or "https://rzp.io/i/PENDING"
                    extras["copy_text"] = msg.replace("{link}", link)
                    extras["copy_source"] = src
                    extras["copy_note"] = note
                    if src == "llm":
                        stats["llm_copy"] += 1
                    elif src == "fallback_rejected":
                        stats["copy_rejected"] += 1

                engine.commit(event, decision)
                won = draw_outcome(rng, event, decision.action)
                if won:
                    recovered = True
                    final_outcome = "recovered"
                    _log(audit, event, arm, attempt_no, decision, final_outcome,
                         event.amount, cost, extras)
                    break
                final_outcome = "attempt_failed"

            _log(audit, event, arm, attempt_no, decision, final_outcome, 0.0,
                 cost, extras)

            if decision.action in (Action.ESCALATE_HUMAN, Action.NO_ACTION):
                break

            now = decision.scheduled_for + timedelta(hours=1)

        # Anyone we never successfully worked can still pay on their own. Not
        # counting this would quietly inflate our incremental number.
        if not recovered and draw_organic(rng, event):
            recovered = True
            final_outcome = "recovered_organic"

        if escalated or cause == RootCause.UNKNOWN or final_outcome == "window_expired":
            exceptions.append({
                "event_id": event.event_id, "amount": event.amount,
                "cause": cause.value, "confidence": round(confidence, 2),
                "error_reason": event.error_reason, "reason": final_outcome,
                "rationale": rationale,
            })

        results["treatment"].append({
            "event": event, "cause": cause,
            "recovered": recovered,
            "recovered_rupees": event.amount if recovered else 0.0,
            "cost": spend, "attempts": attempts,
            "outcome": final_outcome,
        })

    audit.commit()
    stats["gateway_mode"] = "mock" if rz.mock else "live"
    stats["circuit_trips"] = rz.breaker.trips
    stats["gateway_queued"] = len(rz.queued)
    stats["llm_provider"] = llm.provider
    stats["llm_failures"] = llm.failures
    return results, exceptions, engine, audit, stats


def _log(audit, event, arm, attempt_no, decision, outcome, recovered, cost,
         extras=None):
    extras = extras or {}
    audit.write(dict(
        ts=datetime.now().isoformat(timespec="seconds"),
        event_id=event.event_id, customer_id=event.customer_id, arm=arm,
        attempt_no=attempt_no, amount_rupees=event.amount,
        error_reason=event.error_reason, cause=decision.cause.value,
        confidence=decision.confidence, rationale=decision.rationale,
        action=decision.action.value,
        scheduled_for=decision.scheduled_for.isoformat(timespec="minutes"),
        expected_value=round(decision.expected_value, 2),
        gates_json=json.dumps([asdict(g) for g in decision.gates]),
        gate_runs_json=json.dumps(decision.gate_runs),
        blocked_json=json.dumps(decision.blocked_actions),
        notes=decision.notes, outcome=outcome,
        recovered_rupees=recovered, cost_rupees=cost,
        diag_source=extras.get("diag_source", "rules"),
        copy_text=extras.get("copy_text", ""),
        copy_source=extras.get("copy_source", ""),
        copy_note=extras.get("copy_note", ""),
        gateway_status=extras.get("gateway_status", ""),
        payment_link=extras.get("payment_link", ""),
    ))


# -------------------------------------------------------------------- metrics

def compute_metrics(results, exceptions, engine):
    t, c = results["treatment"], results["control"]

    def arm_stats(arm):
        n = len(arm)
        at_risk = sum(r["event"].amount for r in arm)
        rec_n = sum(1 for r in arm if r["recovered"])
        rec_v = sum(r["recovered_rupees"] for r in arm)
        cost = sum(r["cost"] for r in arm)
        return {
            "n": n,
            "at_risk_rupees": at_risk,
            "recovered_count": rec_n,
            "recovered_rupees": rec_v,
            "recovery_rate": rec_n / n if n else 0.0,
            "value_recovery_rate": rec_v / at_risk if at_risk else 0.0,
            "action_cost_rupees": cost,
        }

    ts, cs = arm_stats(t), arm_stats(c)

    lift_pp = (ts["recovery_rate"] - cs["recovery_rate"]) * 100
    value_lift = ts["value_recovery_rate"] - cs["value_recovery_rate"]
    incremental_rupees = value_lift * ts["at_risk_rupees"]
    net = incremental_rupees - ts["action_cost_rupees"]
    roi = (net / ts["action_cost_rupees"]) if ts["action_cost_rupees"] else 0.0

    by_cause = {}
    for r in t:
        k = r["cause"].value
        b = by_cause.setdefault(k, {"n": 0, "recovered": 0, "at_risk": 0.0,
                                    "recovered_rupees": 0.0, "cost": 0.0})
        b["n"] += 1
        b["recovered"] += 1 if r["recovered"] else 0
        b["at_risk"] += r["event"].amount
        b["recovered_rupees"] += r["recovered_rupees"]
        b["cost"] += r["cost"]
    ctrl_by_cause = {}
    for r in c:
        k = r["cause"].value
        b = ctrl_by_cause.setdefault(k, {"n": 0, "recovered": 0})
        b["n"] += 1
        b["recovered"] += 1 if r["recovered"] else 0
    for k, b in by_cause.items():
        cb = ctrl_by_cause.get(k, {"n": 0, "recovered": 0})
        b["rate"] = b["recovered"] / b["n"] if b["n"] else 0.0
        b["control_rate"] = cb["recovered"] / cb["n"] if cb["n"] else None
        b["lift_pp"] = ((b["rate"] - b["control_rate"]) * 100
                        if b["control_rate"] is not None else None)

    return {
        "treatment": ts,
        "control": cs,
        "lift_pp": lift_pp,
        "incremental_rupees": incremental_rupees,
        "net_rupees": net,
        "roi": roi,
        "cost_per_recovered_rupee": (ts["action_cost_rupees"] / incremental_rupees
                                     if incremental_rupees > 0 else None),
        "by_cause": by_cause,
        "exceptions": exceptions,
        "exception_rate": len(exceptions) / len(t) if t else 0.0,
        "gate_blocks": dict(sorted(engine.gate_block_counts.items(),
                                   key=lambda kv: -kv[1])),
    }
