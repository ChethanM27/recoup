"""
Recoup - one-command batch run.

    python3 run_sim.py                 # 600 events, default guardrails
    python3 run_sim.py --n 1200        # bigger cohort
    python3 run_sim.py --db recoup.db  # persist the audit trail to disk
"""
import argparse

from recoup.generator import generate_cohort
from recoup.engine import run_batch, compute_metrics
from recoup.policy import Guardrails

R = "\u20b9"


def money(x):
    return f"{R}{x:,.0f}"


def bar(frac, width=28):
    f = max(0.0, min(1.0, frac))
    return "\u2588" * int(round(f * width)) + "\u00b7" * (width - int(round(f * width)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--holdout", type=float, default=0.20)
    args = ap.parse_args()

    events = generate_cohort(n=args.n, seed=args.seed)
    results, exceptions, engine, audit, stats = run_batch(
        events, Guardrails(), seed=args.seed + 4,
        db_path=args.db, holdout=args.holdout)
    m = compute_metrics(results, exceptions, engine)

    t, c = m["treatment"], m["control"]

    print()
    print("=" * 74)
    print(f"  RECOUP  |  batch of {len(events)} at-risk events  |  "
          f"{money(t['at_risk_rupees'] + c['at_risk_rupees'])} at risk")
    print("=" * 74)

    print("\n  ARMS")
    print(f"    treatment   n={t['n']:>4}   at risk {money(t['at_risk_rupees']):>12}   "
          f"recovered {t['recovery_rate']*100:5.1f}%  {bar(t['recovery_rate'])}")
    print(f"    control     n={c['n']:>4}   at risk {money(c['at_risk_rupees']):>12}   "
          f"recovered {c['recovery_rate']*100:5.1f}%  {bar(c['recovery_rate'])}")

    print("\n  THE ONLY NUMBER THAT COUNTS")
    print(f"    lift over holdout          {m['lift_pp']:+.1f} pp")
    print(f"    incremental revenue        {money(m['incremental_rupees'])}")
    print(f"    cost of every action       {money(t['action_cost_rupees'])}")
    print(f"    net                        {money(m['net_rupees'])}")
    print(f"    return on recovery spend   {m['roi']:.0f}x")
    gross = money(t['recovered_rupees'])
    print(f"\n    (a naive dashboard would have reported {gross} here, by "
          f"\n     counting the {c['recovery_rate']*100:.0f}% who pay us with no help at all)")

    print("\n  BY ROOT CAUSE")
    print(f"    {'cause':<20}{'n':>5}{'recov':>8}{'ctrl':>8}{'lift':>8}"
          f"{'at risk':>13}{'spend':>9}")
    for k, b in sorted(m["by_cause"].items(), key=lambda kv: -kv[1]["at_risk"]):
        ctrl = f"{b['control_rate']*100:.0f}%" if b["control_rate"] is not None else "  -"
        lift = f"{b['lift_pp']:+.0f}pp" if b["lift_pp"] is not None else "   -"
        print(f"    {k:<20}{b['n']:>5}{b['rate']*100:>7.0f}%{ctrl:>8}{lift:>8}"
              f"{money(b['at_risk']):>13}{money(b['cost']):>9}")

    print("\n  GUARDRAILS THAT FIRED  (actions we chose not to take)")
    if not m["gate_blocks"]:
        print("    none")
    for gate, n in list(m["gate_blocks"].items())[:12]:
        print(f"    {gate:<28}{n:>5}")

    print(f"\n  EXCEPTIONS  ({len(exceptions)} of {t['n']}, "
          f"{m['exception_rate']*100:.1f}% - handed to a human, not guessed at)")
    for e in exceptions[:6]:
        print(f"    {e['event_id']}  {money(e['amount']):>10}  {e['cause']:<18}"
              f"conf {e['confidence']:.2f}  {e['reason']}")
    if len(exceptions) > 6:
        print(f"    ... and {len(exceptions) - 6} more")

    print("\n  RUN PROVENANCE")
    print(f"    gateway mode               {stats['gateway_mode']}"
          f"   (payment links created: {stats['links_created']}, "
          f"queued: {stats['links_queued']})")
    print(f"    model provider             {stats['llm_provider']}")
    print(f"    classified by rules        {stats['rule_classified']}")
    print(f"    escalated to the model     {stats['llm_diagnoses']}")
    print(f"    copy written by model      {stats['llm_copy']}"
          f"   (rejected by validator: {stats['copy_rejected']})")

    if args.db != ":memory:":
        rows = audit.query("SELECT COUNT(*) AS n FROM audit")[0]["n"]
        print(f"\n  audit trail: {rows} decisions written to {args.db}")
    print()


if __name__ == "__main__":
    main()
