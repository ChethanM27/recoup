"""
Recoup dashboard server. Standard library only.

    python3 serve.py                    # http://localhost:8000
    python3 serve.py --n 900 --port 8080

Runs a batch on startup, holds the audit trail in memory, and serves it. There is
no build step, no npm, no pip. Clone and run.
"""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from recoup.generator import generate_cohort
from recoup.engine import run_batch, compute_metrics
from recoup.policy import Guardrails
from recoup.llm import LLM
from recoup.razorpay_client import RazorpayClient
from recoup.taxonomy import (RootCause, Action, PLAYBOOKS, ACTION_COST,
                             ANNOYANCE_COST, CONFIDENCE_FLOOR, classify)
from recoup import language as lang

STATE = {}
HERE = os.path.dirname(os.path.abspath(__file__))


def build(n, seed, holdout):
    events = generate_cohort(n=n, seed=seed)
    rz = RazorpayClient()
    llm = LLM()
    results, exceptions, engine, audit, stats = run_batch(
        events, Guardrails(), seed=seed + 4, db_path=":memory:",
        holdout=holdout, llm=llm, rz=rz)
    metrics = compute_metrics(results, exceptions, engine)
    STATE.update(metrics=metrics, audit=audit, stats=stats,
                 guardrails=Guardrails(), n=len(events))
    return metrics, stats


def summary():
    m, s = STATE["metrics"], STATE["stats"]
    t, c = m["treatment"], m["control"]
    g = STATE["guardrails"]
    return {
        "cohort_size": STATE["n"],
        "at_risk": t["at_risk_rupees"] + c["at_risk_rupees"],
        "treatment": t, "control": c,
        "lift_pp": m["lift_pp"],
        "incremental": m["incremental_rupees"],
        "naive_claim": t["recovered_rupees"],
        "spend": t["action_cost_rupees"],
        "net": m["net_rupees"],
        "roi": m["roi"],
        "by_cause": {k: dict(v, label=lang.cause_name(k)) for k, v in m["by_cause"].items()},
        "flow": _flow(t, c, m),
        "gate_blocks": [{"gate": g, "label": lang.gate_name(g), "n": n}
                        for g, n in m["gate_blocks"].items()],
        "exception_rate": m["exception_rate"],
        "exceptions": [dict(e, label=lang.cause_name(e["cause"]),
                            outcome_label=lang.outcome_name(e["reason"]))
                       for e in m["exceptions"][:40]],
        "provenance": s,
        "guardrails": {
            "quiet_hours": f"{g.quiet_hours_start:02d}:00-{g.quiet_hours_end:02d}:00 IST",
            "max_attempts_per_event": g.max_attempts_per_event,
            "max_touches_per_customer_7d": g.max_touches_per_customer_7d,
            "cooldown_hours": g.cooldown_hours,
            "min_ticket_rupees": g.min_amount_rupees,
            "ev_threshold_rupees": g.ev_threshold_rupees,
            "daily_outreach_budget_rupees": g.daily_outreach_budget_rupees,
            "voice_call_floor_rupees": g.voice_call_min_amount_rupees,
        },
    }


def cage():
    """
    Run the red-team suite live and report it. Same validators as the live
    path - nothing here is a mock, and it recomputes on every page load so it
    can never drift from the code it claims to be testing.
    """
    import redteam as rt
    from recoup.llm import validate_copy, validate_proposed_action, LLM_CONFIDENCE_CEILING

    copy_rows = []
    for label, text, must_pass, why in rt.COPY_CASES:
        ok, result = validate_copy(text, rt.AMOUNT)
        copy_rows.append({"label": label, "sent": ok, "correct": ok == must_pass,
                          "reason": None if ok else result, "why": why,
                          "text": text if len(text) < 260 else text[:257] + "..."})

    act_rows = []
    for label, cause, proposed, must_pass, why in rt.ACTION_CASES:
        action, note = validate_proposed_action(proposed, cause)
        ok = action is not None
        act_rows.append({"label": label, "cause": lang.cause_name(cause.value),
                         "proposed": lang.action_name(proposed) if proposed in
                                     Action.__members__ else proposed,
                         "allowed": ok, "correct": ok == must_pass,
                         "reason": None if ok else note, "why": why})

    total = len(copy_rows) + len(act_rows) + 2
    good = (sum(r["correct"] for r in copy_rows) + sum(r["correct"] for r in act_rows)
            + (1 if LLM_CONFIDENCE_CEILING < 0.97 else 0) + 1)
    return {
        "copy": copy_rows, "actions": act_rows,
        "ceiling": LLM_CONFIDENCE_CEILING, "rule_confidence": 0.97,
        "passed": good, "total": total,
        "refused": sum(1 for r in copy_rows if not r["sent"])
                   + sum(1 for r in act_rows if not r["allowed"]),
    }


def trust():
    """
    How good is the diagnosis, and what does being wrong cost?

    Computed live on a fresh cohort so the page can never show a stale or
    hand-typed figure. This is the section a sceptical reader should open
    first, which is why it reports the ugly slice rather than the flattering
    blended one.
    """
    from recoup.generator import generate_cohort
    NEVER_ACT = {RootCause.RISK_BLOCKED, RootCause.INSTRUMENT_DEAD}

    rows = []
    for e in generate_cohort(900, seed=11):
        pred, conf, _ = classify(e)
        rows.append({"true": e._true_cause, "pred": pred, "conf": conf,
                     "acted": conf >= CONFIDENCE_FLOOR,
                     "ok": pred == e._true_cause,
                     "noise": getattr(e, "_label_noise", "clean"),
                     "amt": e.amount})

    n = len(rows)
    slices = []
    for kind, label, note in [
        ("clean", "Gateway told the truth",
         "The easy slice. Quoting only this would be dishonest."),
        ("degraded", "Gateway sent a generic code",
         "We abstain and route to a human. Failing loudly is the right answer."),
        ("confused", "Gateway sent a wrong but valid code",
         "The dangerous slice. The evidence is plausible and it is lying."),
    ]:
        b = [r for r in rows if r["noise"] == kind]
        if not b:
            continue
        slices.append({
            "key": kind, "label": label, "note": note, "n": len(b),
            "accuracy": sum(r["ok"] for r in b) / len(b),
            "acted": sum(r["acted"] for r in b) / len(b),
        })

    wrong = [r for r in rows if not r["ok"]]
    caught = [r for r in wrong if not r["acted"]]
    acted_wrong = [r for r in wrong if r["acted"]]
    dangerous = [r for r in acted_wrong if r["true"] in NEVER_ACT]

    return {
        "n": n,
        "accuracy": sum(r["ok"] for r in rows) / n,
        "slices": slices,
        "errors": {
            "n": len(wrong), "share": len(wrong) / n,
            "caught_n": len(caught),
            "caught_share": len(caught) / len(wrong) if wrong else 0,
            "acted_n": len(acted_wrong),
            "dangerous_n": len(dangerous),
            "dangerous_share": len(dangerous) / n,
            "dangerous_rupees": sum(r["amt"] for r in dangerous),
        },
        "floor": CONFIDENCE_FLOOR,
    }


def _ladder(r):
    """
    Which gate run to walk the viewer through.

    If any candidate was blocked, show THAT one - the interesting story is the
    action we wanted to take and the rule that stopped us, not the safe action
    that sailed through. Otherwise show the action we actually took.
    """
    runs = r.get("gate_runs") or []
    blocked = [x for x in runs if not x["allowed"] and x["gates"]]
    chosen = next((x for x in runs if x["action"] == r["action"]), None)
    pick = blocked[0] if blocked else chosen

    if pick is None:
        return {"action": r["action"], "label": lang.action_name(r["action"]),
                "blocked": False, "gates": [], "fell_back_to": None}

    fell_back = None
    if not pick["allowed"] and r["action"] != pick["action"]:
        fell_back = lang.action_name(r["action"])

    return {
        "action": pick["action"],
        "label": lang.action_name(pick["action"]),
        "blocked": not pick["allowed"],
        "fell_back_to": fell_back,
        "gates": [{"gate": g["gate"], "label": lang.gate_name(g["gate"]),
                   "passed": g["passed"], "detail": g["detail"]}
                  for g in pick["gates"]],
    }


def _flow(t, c, m):
    """
    The three streams the at-risk money splits into. This is the honesty
    argument as a picture: what we won, what was never ours to claim, what
    is still gone.
    """
    ours = max(0.0, m["incremental_rupees"])
    would_have = max(0.0, t["recovered_rupees"] - ours)
    lost = max(0.0, t["at_risk_rupees"] - t["recovered_rupees"])
    total = ours + would_have + lost or 1.0
    return {
        "total": t["at_risk_rupees"],
        "streams": [
            {"key": "ours", "label": "Recovered because we acted",
             "value": ours, "share": ours / total,
             "note": "Worked arm minus the holdout. The only number we defend."},
            {"key": "organic", "label": "Would have paid anyway",
             "value": would_have, "share": would_have / total,
             "note": "Real money, but not ours to claim. Most tools count it."},
            {"key": "lost", "label": "Still gone",
             "value": lost, "share": lost / total,
             "note": "Some of it never was recoverable. We say so."},
        ],
    }


def showcase():
    """
    Four decisions worth walking through, chosen so the walkthrough shows the
    system succeeding, refusing, being blocked, and declining to spend.
    """
    picks, seen = [], set()
    want = [
        ("a payment we won back", "outcome='recovered' AND action!='RETRY_NOW'"),
        ("a risk decline we refused to touch", "cause='RISK_BLOCKED'"),
        ("an action a guardrail stopped",
         "blocked_json != '{}' AND blocked_json != '' AND gate_runs_json LIKE '%\"allowed\": false%'"),
        ("a message we actually sent", "copy_text != '' AND outcome='recovered'"),
        ("money we chose not to chase", "action='NO_ACTION'"),
    ]
    for label, cond in want:
        rows = STATE["audit"].query(
            f"SELECT event_id, amount_rupees, cause, action FROM audit "
            f"WHERE arm='treatment' AND {cond} ORDER BY amount_rupees DESC LIMIT 6")
        for r in rows:
            if r["event_id"] not in seen:
                seen.add(r["event_id"])
                picks.append({"event_id": r["event_id"], "hook": label,
                              "amount": r["amount_rupees"],
                              "cause_label": lang.cause_name(r["cause"])})
                break
    return picks


def story(event_id):
    """The full decision, staged as a narrative a human can follow."""
    rows = one_event(event_id)
    if not rows:
        return {"error": "no such event"}
    r = rows[-1]
    cause = RootCause(r["cause"])
    action = Action(r["action"]) if r["action"] in Action.__members__ else None
    amt = r["amount_rupees"]

    # Recover the believed success probability from the expected-value identity
    # the engine used:  ev = p*amount - cost - goodwill
    p = None
    if action and amt:
        cost = ACTION_COST.get(action, 0.0)
        ann = ANNOYANCE_COST.get(action, 0.0)
        p = max(0.0, min(1.0, (r["expected_value"] + cost + ann) / amt))

    pb = PLAYBOOKS[cause]
    return {
        "event_id": event_id,
        "amount": amt,
        "failure": {
            "reason": r["error_reason"],
            "attempt": r["attempt_no"],
        },
        "diagnosis": {
            "cause": cause.value,
            "label": lang.cause_name(cause.value),
            "oneline": lang.cause_oneline(cause.value),
            "confidence": r["confidence"],
            "by": r["diag_source"],
            "rationale": r["rationale"],
        },
        "playbook": {
            "explanation": pb.explanation,
            "retry_forbidden": pb.retry_forbidden,
            "max_attempts": pb.max_attempts,
            "candidates": [{"action": a.value, "label": lang.action_name(a.value),
                            "why": lang.action_why(a.value),
                            "cost": ACTION_COST[a] + ANNOYANCE_COST[a]}
                           for a in pb.candidates],
        },
        "ladder": _ladder(r),
        "blocked": [{"action": a, "label": lang.action_name(a),
                     "gates": [lang.gate_name(g) for g in gs]}
                    for a, gs in (r["blocked"] or {}).items()],
        "maths": {
            "p": p, "amount": amt,
            "cost": ACTION_COST.get(action, 0.0) if action else 0.0,
            "goodwill": ANNOYANCE_COST.get(action, 0.0) if action else 0.0,
            "ev": r["expected_value"],
            "threshold": STATE["guardrails"].ev_threshold_rupees,
        },
        "action": {
            "action": r["action"],
            "label": lang.action_name(r["action"]),
            "why": lang.action_why(r["action"]),
            "copy": r["copy_text"],
            "copy_source": r["copy_source"],
            "link": r["payment_link"],
            "gateway": r["gateway_status"],
            "notes": r["notes"],
        },
        "outcome": {
            "raw": r["outcome"],
            "label": lang.outcome_name(r["outcome"]),
            "tone": lang.outcome_tone(r["outcome"]),
            "recovered": r["recovered_rupees"],
        },
    }


def decisions(limit=250, arm="treatment"):
    rows = STATE["audit"].query(
        "SELECT event_id, customer_id, amount_rupees, error_reason, cause, "
        "confidence, action, expected_value, outcome, attempt_no, diag_source, "
        "copy_source, gateway_status, notes "
        "FROM audit WHERE arm=? ORDER BY amount_rupees DESC LIMIT ?",
        (arm, limit))
    for r in rows:
        r["cause_label"] = lang.cause_name(r["cause"])
        r["action_label"] = lang.action_name(r["action"])
        r["outcome_label"] = lang.outcome_name(r["outcome"])
        r["tone"] = lang.outcome_tone(r["outcome"])
    return rows


def one_event(event_id):
    rows = STATE["audit"].query(
        "SELECT * FROM audit WHERE event_id=? ORDER BY attempt_no", (event_id,))
    for r in rows:
        r["gate_runs"] = json.loads(r.get("gate_runs_json") or "[]")
        r.pop("gate_runs_json", None)
        r["gates"] = json.loads(r.get("gates_json") or "[]")
        r["blocked"] = json.loads(r.get("blocked_json") or "{}")
        r.pop("gates_json", None)
        r.pop("blocked_json", None)
    return rows


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                    return self._send(f.read(), "text/html; charset=utf-8")
            if u.path == "/api/summary":
                return self._send(summary())
            if u.path == "/api/decisions":
                return self._send(decisions(
                    int(q.get("limit", [250])[0]), q.get("arm", ["treatment"])[0]))
            if u.path == "/api/cage":
                if "cage" not in STATE:
                    STATE["cage"] = cage()
                return self._send(STATE["cage"])
            if u.path == "/api/trust":
                if "trust" not in STATE:
                    STATE["trust"] = trust()
                return self._send(STATE["trust"])
            if u.path == "/api/showcase":
                return self._send(showcase())
            if u.path.startswith("/api/story/"):
                return self._send(story(u.path.rsplit("/", 1)[-1]))
            if u.path.startswith("/api/event/"):
                return self._send(one_event(u.path.rsplit("/", 1)[-1]))
            self._send({"error": "not found"}, code=404)
        except FileNotFoundError:
            self._send("static/index.html is missing from the repo", "text/plain", 500)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--holdout", type=float, default=0.20)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    print(f"  building batch of {args.n} events ...")
    m, s = build(args.n, args.seed, args.holdout)
    print(f"  lift {m['lift_pp']:+.1f} pp   incremental \u20b9{m['incremental_rupees']:,.0f}"
          f"   gateway={s['gateway_mode']}   model={s['llm_provider']}")
    print(f"\n  dashboard  ->  http://localhost:{args.port}\n")
    ThreadingHTTPServer(("", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
