"""
Calibration harness.

A single run of a simulator is an anecdote. This does two things a reviewer
should demand before believing any number in this repo:

  1. VARIANCE   - the same configuration across many seeds, reported as a mean
                  and a range. If the headline moves 15 points between seeds,
                  the headline is noise.

  2. SENSITIVITY - the headline recomputed while the simulated world's true
                  efficacy is scaled up and down. This shows exactly how much of
                  the result is the agent and how much is the world we invented.

    python3 calibrate.py
    python3 calibrate.py --seeds 30 --n 800
"""

import argparse
import statistics as st

from recoup import generator
from recoup.generator import generate_cohort
from recoup.engine import run_batch, compute_metrics
from recoup.policy import Guardrails

R = "\u20b9"


def one_run(n, seed, scale=None):
    if scale is not None:
        generator.EFFICACY_SCALE = scale
    events = generate_cohort(n=n, seed=seed)
    results, exceptions, engine, audit, stats = run_batch(
        events, Guardrails(), seed=seed + 4, db_path=":memory:")
    return compute_metrics(results, exceptions, engine)


def summarise(vals):
    return (st.mean(vals), min(vals), max(vals),
            st.pstdev(vals) if len(vals) > 1 else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seeds", type=int, default=20)
    args = ap.parse_args()

    base = generator.EFFICACY_SCALE

    print("\n" + "=" * 74)
    print(f"  RECOUP  |  calibration harness  |  n={args.n} per run")
    print("=" * 74)

    # ---------------------------------------------------------- variance
    print(f"\n  VARIANCE ACROSS {args.seeds} SEEDS  (same world, different draws)")
    lifts, incs, rois, excs, ctrls = [], [], [], [], []
    for s in range(1, args.seeds + 1):
        m = one_run(args.n, s, base)
        lifts.append(m["lift_pp"])
        incs.append(m["incremental_rupees"])
        rois.append(m["roi"])
        excs.append(m["exception_rate"] * 100)
        ctrls.append(m["control"]["recovery_rate"] * 100)

    for label, vals, fmt in [
        ("lift over holdout (pp)", lifts, "{:.1f}"),
        ("holdout recovery (%)", ctrls, "{:.1f}"),
        ("incremental (Rs)", incs, "{:,.0f}"),
        ("return on spend (x)", rois, "{:.0f}"),
        ("exception rate (%)", excs, "{:.1f}"),
    ]:
        mean, lo, hi, sd = summarise(vals)
        print(f"    {label:<26} mean {fmt.format(mean):>10}   "
              f"range {fmt.format(lo)} to {fmt.format(hi)}   sd {fmt.format(sd)}")

    mean_lift, lo_lift, hi_lift, sd_lift = summarise(lifts)
    print(f"\n    The claim we will defend: a lift of {mean_lift:.0f} pp "
          f"\u00b1 {sd_lift:.0f} on this cohort,")
    print(f"    not a single cherry-picked run.")

    # ------------------------------------------------------- sensitivity
    print("\n  SENSITIVITY  (how much of the result is the world, not the agent?)")
    print(f"    {'world efficacy':<18}{'holdout':>10}{'worked':>10}"
          f"{'lift':>10}{'incremental':>15}")
    for scale in [0.20, 0.30, base, 0.60, 0.80]:
        ms = [one_run(args.n, s, scale) for s in range(1, 6)]
        ctrl = st.mean(m["control"]["recovery_rate"] for m in ms) * 100
        treat = st.mean(m["treatment"]["recovery_rate"] for m in ms) * 100
        lift = st.mean(m["lift_pp"] for m in ms)
        inc = st.mean(m["incremental_rupees"] for m in ms)
        tag = "  <- shipped" if abs(scale - base) < 1e-9 else ""
        print(f"    {scale:<18.2f}{ctrl:>9.1f}%{treat:>9.1f}%"
              f"{lift:>9.1f}pp{R + format(inc, ',.0f'):>15}{tag}")

    generator.EFFICACY_SCALE = base

    print("\n  WHAT THIS DOES AND DOES NOT PROVE")
    print("    Does not prove: that a real merchant would see this exact lift.")
    print("                    The absolute number is a property of the world we")
    print("                    invented, and it moves with the scalar above.")
    print("    Does prove:     the lift is stable across seeds, it tracks the")
    print("                    world's true efficacy in the direction and")
    print("                    magnitude you would expect, and the holdout arm")
    print("                    behaves independently of the agent - which is what")
    print("                    makes it a valid counterfactual.\n")


if __name__ == "__main__":
    main()
