#!/usr/bin/env python3
"""
Classifier evaluation.

The taxonomy is the load-bearing part of this system: every playbook, gate and
expected-value calculation hangs off the diagnosed cause. If the diagnosis is
wrong, everything downstream is confidently wrong. So it gets scored.

    python3 evaluate.py --n 2000

Three things are measured, in increasing order of how much they matter:

  1. Accuracy. Reported split by label quality, because the easy slice is
     circular by construction and quoting a blended number would be dishonest.
  2. Where the errors go. A confusion matrix, plus precision/recall per cause.
  3. What the errors COST. An error the confidence floor catches is an
     exception ticket. An error it doesn't catch becomes an action taken
     against a real customer. Only the second kind can hurt anyone.
"""

import argparse
from collections import Counter, defaultdict

from recoup.generator import generate_cohort
from recoup.taxonomy import classify, RootCause, PLAYBOOKS
from recoup.policy import CONFIDENCE_FLOOR
from recoup import language as lang

CAUSES = list(RootCause)
SHORT = {
    RootCause.TRANSIENT_RAIL: "RAIL", RootCause.INSUFFICIENT_FUNDS: "FUNDS",
    RootCause.AUTH_FRICTION: "AUTH", RootCause.INSTRUMENT_DEAD: "DEAD",
    RootCause.RISK_BLOCKED: "RISK", RootCause.CUSTOMER_ABANDON: "ABND",
    RootCause.MANDATE_FAILURE: "MNDT", RootCause.UNKNOWN: "UNKN",
}


def bar(frac, w=22):
    n = int(round(max(0.0, min(1.0, frac)) * w))
    return "█" * n + "·" * (w - n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    events = generate_cohort(args.n, seed=args.seed)
    rows = []
    for e in events:
        pred, conf, _ = classify(e)
        rows.append({
            "true": e._true_cause, "pred": pred, "conf": conf,
            "noise": e._label_noise, "amount": e.amount_paise / 100.0,
            "acted": conf >= CONFIDENCE_FLOOR and pred != RootCause.UNKNOWN,
        })

    n = len(rows)
    correct = sum(1 for r in rows if r["true"] == r["pred"])

    print()
    print("=" * 74)
    print("  CLASSIFIER EVALUATION".ljust(56) + f"n = {n}".rjust(18))
    print("=" * 74)

    # ---------------------------------------------------------- 1. accuracy
    print("\n  ACCURACY, SPLIT BY WHAT THE GATEWAY TOLD US")
    print("    A blended number would flatter us. The clean slice is easy by")
    print("    construction - the reported code maps straight to the cause.\n")

    order = [("clean", "code matched reality"),
             ("degraded", "code went generic"),
             ("confused", "code was wrong but valid")]
    for kind, desc in order:
        sub = [r for r in rows if r["noise"] == kind]
        if not sub:
            continue
        acc = sum(1 for r in sub if r["true"] == r["pred"]) / len(sub)
        print(f"    {kind:<9} n={len(sub):>5}  {acc:>6.1%}  {bar(acc)}  {desc}")
    print(f"\n    {'OVERALL':<9} n={n:>5}  {correct/n:>6.1%}  {bar(correct/n)}")

    # -------------------------------------------------- 2. confusion matrix
    cm = defaultdict(Counter)
    for r in rows:
        cm[r["true"]][r["pred"]] += 1

    print("\n\n  CONFUSION MATRIX          predicted \u2192")
    hdr = "    " + " " * 9 + "".join(f"{SHORT[c]:>7}" for c in CAUSES)
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))
    for t in CAUSES:
        row = cm[t]
        tot = sum(row.values())
        if not tot:
            continue
        cells = ""
        for p in CAUSES:
            v = row[p]
            cells += f"{'.' if v == 0 else v:>7}"
        print(f"    {SHORT[t]:<9}{cells}   ({tot})")
    print("\n    Diagonal is correct. Everything off it is a wrong playbook.")

    # ------------------------------------------- 3. precision / recall / F1
    print("\n\n  PER-CAUSE")
    print(f"    {'cause':<26}{'prec':>7}{'recall':>8}{'F1':>7}{'n':>7}")
    print("    " + "-" * 55)
    for c in CAUSES:
        tp = cm[c][c]
        fn = sum(cm[c].values()) - tp
        fp = sum(cm[t][c] for t in CAUSES if t != c)
        if tp + fn == 0:
            continue
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn)
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        print(f"    {lang.cause_name(c.value):<26}{prec:>7.2f}{rec:>8.2f}"
              f"{f1:>7.2f}{tp+fn:>7}")

    # ---------------------------------------------------- 4. is it calibrated
    print("\n\n  IS THE CONFIDENCE HONEST?")
    print("    A confidence score is only useful if it tracks being right.\n")
    buckets = [(0.0, 0.55, "below floor"), (0.55, 0.80, "0.55 - 0.80"),
               (0.80, 0.96, "0.80 - 0.96"), (0.96, 1.01, "0.96 +")]
    for lo, hi, label in buckets:
        sub = [r for r in rows if lo <= r["conf"] < hi]
        if not sub:
            continue
        acc = sum(1 for r in sub if r["true"] == r["pred"]) / len(sub)
        print(f"    {label:<14} n={len(sub):>5}  actually right {acc:>6.1%}  {bar(acc)}")

    # ------------------------------------------------- 5. what errors cost
    wrong = [r for r in rows if r["true"] != r["pred"]]
    caught = [r for r in wrong if not r["acted"]]
    escaped = [r for r in wrong if r["acted"]]

    # The error that actually matters: a non-retryable cause misread as
    # something we are allowed to retry or chase.
    NON_RETRYABLE = {RootCause.RISK_BLOCKED, RootCause.INSTRUMENT_DEAD}
    dangerous = [r for r in escaped
                 if r["true"] in NON_RETRYABLE
                 and not PLAYBOOKS[r["pred"]].retry_forbidden]

    print("\n\n  WHAT THE ERRORS COST")
    print("    Not all wrong answers are equal. One becomes a ticket a human")
    print("    reads. The other becomes an action taken against a customer.\n")
    print(f"    wrong predictions                {len(wrong):>6}"
          f"   ({len(wrong)/n:.1%} of cohort)")
    print(f"      caught by the confidence floor {len(caught):>6}"
          f"   ({len(caught)/max(1,len(wrong)):.0%} of errors) -> human review")
    print(f"      acted on anyway                {len(escaped):>6}"
          f"   ({len(escaped)/max(1,len(wrong)):.0%} of errors)")
    print(f"\n    of those acted on, ones where a NON-RETRYABLE cause was")
    print(f"    misread as retryable:            {len(dangerous):>6}"
          f"   ({len(dangerous)/n:.2%} of cohort)")

    if dangerous:
        amt = sum(r["amount"] for r in dangerous)
        print(f"      exposure                       \u20b9{amt:>11,.0f}")
        mix = Counter((r["true"], r["pred"]) for r in dangerous).most_common(3)
        print("      most common:")
        for (t, p), c in mix:
            print(f"        {lang.cause_name(t.value)} read as "
                  f"{lang.cause_name(p.value)}  \u00d7{c}")
        print("\n    These are the ones a live rollout has to solve. The gates")
        print("    stop the action being harmful, but they cannot un-wrong a")
        print("    diagnosis - that needs the issuer's real decline code.")
    else:
        print("      none in this cohort.")

    print("\n" + "=" * 74)
    print("  The clean-slice number is not a claim. The degraded and confused")
    print("  slices are, and they are where a live deployment actually lives.")
    print("=" * 74 + "\n")


if __name__ == "__main__":
    main()
