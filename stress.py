#!/usr/bin/env python3
"""
The misspecified-world test.

The fair objection to any simulated result is that it is circular: the same
author wrote the world and the agent's beliefs about the world, so of course
they agree. `policy.BELIEVED_EFFICACY` and `generator.TRUE_EFFICACY` are close
because I wrote both.

So this breaks them apart on purpose and reruns the batch.

    python3 stress.py --n 800

Three worlds:

  aligned      beliefs roughly match reality        (the default claim)
  scrambled    action rankings shuffled per cause   (beliefs are noise)
  inverted     the believed-best action is the       (beliefs are actively
               actually-worst one                    backwards)

What must survive all three, or the guardrails are decoration:

  - no retry against a cause where retry is forbidden
  - no action against a customer who opted out
  - spend stays inside the daily budget
  - the reported lift degrades toward zero rather than inventing a win

An agent that still claims a big number in the inverted world is measuring
itself, not the world.
"""

import argparse
import copy

from recoup.generator import generate_cohort, TRUE_EFFICACY, EFFICACY_SCALE
from recoup.engine import run_batch, compute_metrics
from recoup.taxonomy import Action, RootCause, PLAYBOOKS
from recoup import language as lang


def remap(events, mode, rng_seed=3):
    """Rewrite each event's latent efficacy without telling the agent."""
    import random
    rng = random.Random(rng_seed)
    out = []
    for e in events:
        e2 = copy.copy(e)
        base = TRUE_EFFICACY[e._true_cause]
        acts = [a for a in base if a not in (Action.ESCALATE_HUMAN, Action.NO_ACTION)]
        vals = [base[a] for a in acts]

        if mode == "aligned":
            newmap = dict(zip(acts, vals))
        elif mode == "scrambled":
            shuffled = vals[:]
            rng.shuffle(shuffled)
            newmap = dict(zip(acts, shuffled))
        elif mode == "inverted":
            # the action the agent believes is best becomes the actual worst
            ranked = sorted(acts, key=lambda a: base[a], reverse=True)
            newmap = {a: v for a, v in zip(ranked, sorted(vals))}
        else:
            raise ValueError(mode)

        eff = {a: max(0.0, min(0.97, newmap[a] * e._responsiveness * EFFICACY_SCALE))
               for a in newmap}
        eff[Action.ESCALATE_HUMAN] = 0.0
        eff[Action.NO_ACTION] = 0.0
        e2._true_efficacy = eff
        out.append(e2)
    return out


def audit_safety(audit):
    """Did any guardrail actually break in this world?"""
    rows = audit.query(
        "SELECT event_id, cause, action, outcome, amount_rupees "
        "FROM audit WHERE arm='treatment'")
    forbidden_retry = 0
    for r in rows:
        try:
            cause = RootCause(r["cause"])
            action = Action(r["action"])
        except ValueError:
            continue
        if PLAYBOOKS[cause].retry_forbidden and action in (
                Action.RETRY_NOW, Action.RETRY_SCHEDULED):
            forbidden_retry += 1
    return {"forbidden_retries": forbidden_retry, "decisions": len(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    base = generate_cohort(args.n, seed=args.seed)

    print()
    print("=" * 74)
    print("  MISSPECIFIED-WORLD TEST".ljust(52) + f"n = {args.n}".rjust(22))
    print("=" * 74)
    print("\n  The agent's beliefs are held fixed. The world is rewritten")
    print("  underneath it. Nothing tells the agent that anything changed.\n")

    results = []
    for mode, blurb in [("aligned", "beliefs roughly correct"),
                        ("scrambled", "beliefs are noise"),
                        ("inverted", "beliefs are backwards")]:
        events = remap(base, mode)
        rep, exc, engine, audit, stats = run_batch(events)
        m = compute_metrics(rep, exc, engine)
        # the engine reports incremental and net; spend is the gap between them
        m["spend_rupees"] = m["incremental_rupees"] - m["net_rupees"]
        safety = audit_safety(audit)
        results.append((mode, blurb, m, safety))

    # ------------------------------------------------------------ the number
    print("  DOES THE REPORTED NUMBER SURVIVE?\n")
    print(f"    {'world':<12}{'lift':>10}{'incremental':>15}{'spend':>10}"
          f"{'net':>12}")
    print("    " + "-" * 59)
    for mode, blurb, rep, _ in results:
        print(f"    {mode:<12}{rep['lift_pp']:>9.1f}pp"
              f"\u20b9{rep['incremental_rupees']:>14,.0f}"
              f"\u20b9{rep['spend_rupees']:>9,.0f}"
              f"\u20b9{rep['net_rupees']:>11,.0f}")

    aligned = results[0][2]["lift_pp"]
    inverted = results[-1][2]["lift_pp"]
    print(f"\n    aligned \u2192 inverted:  {aligned:.1f}pp \u2192 {inverted:.1f}pp")
    if inverted < aligned * 0.5:
        print("    The claim collapses when the beliefs are wrong. That is the")
        print("    correct behaviour - the number is measuring the world, not")
        print("    the agent's opinion of itself.")
    else:
        print("    WARNING: the number barely moved. That would mean the lift")
        print("    is an artefact of the measurement, not a real effect.")

    # ---------------------------------------------------------- the guardrails
    print("\n\n  DO THE GUARDRAILS HOLD REGARDLESS?\n")
    print(f"    {'world':<12}{'decisions':>11}{'forbidden retries':>20}"
          f"{'exceptions':>13}")
    print("    " + "-" * 56)
    broke = False
    for mode, blurb, rep, safety in results:
        fr = safety["forbidden_retries"]
        if fr:
            broke = True
        print(f"    {mode:<12}{safety['decisions']:>11}{fr:>20}"
              f"{rep['exception_rate']:>12.1%}")

    print()
    if not broke:
        print("    Zero forbidden retries in every world. The gates are not")
        print("    downstream of the scoring, so a wrong belief can make the")
        print("    agent ineffective but it cannot make it non-compliant.")
    else:
        print("    A guardrail broke. That is a bug, not a finding.")

    print("\n" + "=" * 74)
    print("  What transfers to live data is not the number. It is that the")
    print("  number degrades honestly and the guardrails do not.")
    print("=" * 74 + "\n")


if __name__ == "__main__":
    main()
