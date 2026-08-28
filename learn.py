#!/usr/bin/env python3
"""
Where the numbers should come from.

`policy.BELIEVED_EFFICACY` is 56 hand-written probabilities. CALIBRATION.md is
blunt about this being the weakest thing in the project: I wrote them, and a
number I invented is not evidence. In production nobody would hand-author them
either - you would learn them from what actually happened.

So this learns them.

    python3 learn.py                    # watch beliefs converge on truth
    python3 learn.py --world inverted   # start with BACKWARDS beliefs

A Beta-Bernoulli posterior per (cause, action). Every completed attempt is one
Bernoulli trial: recovered or not. The posterior mean replaces the hand-written
prior, and the posterior *width* is the thing that matters operationally - a
pair we have tried four times is not something to bet budget on, and the model
should say so rather than quoting a confident 0.5.

The real test is not convergence on a good prior. It is the `inverted` world,
where the agent starts believing the worst action is the best one. A system
that cannot recover from being wrong is a system that only works when you
already knew the answer.

Two invariants hold throughout, and are asserted, not hoped for:

  - Learning only ever moves the ORDERING of actions inside a playbook. It
    cannot add an action, and it cannot resurrect one the playbook forbids.
    No amount of evidence that "retrying dead cards sometimes works" will make
    this system retry a dead card.
  - RISK_BLOCKED stays at zero regardless of observed outcomes. If a risk
    decline ever recovers, that is a data problem to escalate, not a signal to
    start working risk declines.
"""

import argparse
import random

from recoup.taxonomy import RootCause, Action, PLAYBOOKS
from recoup.policy import BELIEVED_EFFICACY, BELIEF_SCALE
from recoup.generator import TRUE_EFFICACY, EFFICACY_SCALE
from recoup import language as lang

BAR = "=" * 78

# Causes where no observed outcome may ever move the belief off zero.
FROZEN = {RootCause.RISK_BLOCKED}

# Below this many observations we do not trust the posterior mean enough to
# act on it, and fall back to the hand-written prior. Roughly where a Beta
# posterior's 90% interval narrows to something you could budget against.
MIN_TRIALS = 25


class Learner:
    """One Beta posterior per (cause, action)."""

    def __init__(self, prior_strength=6.0, beliefs=None):
        self.a, self.b, self.n = {}, {}, {}
        src = beliefs or BELIEVED_EFFICACY
        for cause, actions in src.items():
            for action, p in actions.items():
                # Seed on the SAME scale the observations arrive on. The prior
                # is a belief about raw efficacy; outcomes are drawn at
                # efficacy x scale. Seeding unscaled made thinly-tried actions
                # look strong and starved exploration of the rest.
                q = p * BELIEF_SCALE
                self.a[(cause, action)] = max(.5, q * prior_strength)
                self.b[(cause, action)] = max(.5, (1 - q) * prior_strength)
                self.n[(cause, action)] = 0

    def observe(self, cause, action, recovered):
        k = (cause, action)
        if k not in self.a or cause in FROZEN:
            return
        if recovered:
            self.a[k] += 1
        else:
            self.b[k] += 1
        self.n[k] += 1

    def mean(self, cause, action):
        k = (cause, action)
        if cause in FROZEN:
            return 0.0
        if k not in self.a:
            return 0.0
        return self.a[k] / (self.a[k] + self.b[k])

    def width(self, cause, action):
        """Posterior sd - how much we should distrust our own estimate."""
        k = (cause, action)
        if k not in self.a:
            return 0.0
        a, b = self.a[k], self.b[k]
        return ((a * b) / ((a + b) ** 2 * (a + b + 1))) ** .5

    def trials(self, cause, action):
        return self.n.get((cause, action), 0)

    def sample(self, cause, action, rng):
        """
        Draw from the posterior rather than reading its mean.

        This is Thompson sampling, and it is the reason the aligned agent does
        not get worse by learning. Epsilon-greedy explores blindly: it spends a
        fixed share of attempts on options it already knows are bad. Sampling
        explores in proportion to how uncertain we still are, so a pair we have
        tried 900 times stops being explored while a thin one keeps its chance.
        """
        k = (cause, action)
        if cause in FROZEN or k not in self.a:
            return 0.0
        return rng.betavariate(self.a[k], self.b[k])

    def learned_efficacy(self, fallback=None):
        """
        The table the policy would use, with the safety rails applied.

        Anything under MIN_TRIALS keeps its hand-written prior. Anything
        outside the playbook is dropped entirely - learning reorders, it never
        expands the option set.
        """
        out = {}
        src = fallback or BELIEVED_EFFICACY
        for cause, actions in src.items():
            allowed = set(PLAYBOOKS[cause].candidates)
            row = {}
            for action, prior in actions.items():
                if action not in allowed:
                    continue
                if cause in FROZEN:
                    row[action] = 0.0
                elif self.trials(cause, action) >= MIN_TRIALS:
                    row[action] = self.mean(cause, action)
                else:
                    row[action] = prior * BELIEF_SCALE
            out[cause] = row
        return out


# ──────────────────────────────────────────────────────── the environment

def true_p(cause, action, world=None):
    """
    What actually happens. The world is the world - it never changes.

    An earlier version of this file inverted the world AND the beliefs, which
    made them agree again and produced a meaningless 67% starting score. Only
    the agent is ever wrong here.
    """
    return TRUE_EFFICACY.get(cause, {}).get(action, 0.0) * EFFICACY_SCALE


def starting_beliefs(world):
    """What the agent thinks before it sees any data."""
    if world != "inverted":
        return {c: dict(r) for c, r in BELIEVED_EFFICACY.items()}
    out = {}
    for cause, row in BELIEVED_EFFICACY.items():
        if not row:
            out[cause] = dict(row)
            continue
        vals = sorted(row.values())
        rank = sorted(row.items(), key=lambda kv: kv[1])
        out[cause] = {a: vals[len(vals) - 1 - i] for i, (a, _) in enumerate(rank)}
    return out


def simulate(world, rounds, per_round, seed=7, thompson=True):
    """
    Run the loop. Each round: choose an action, observe, update the posterior.

    Selection is Thompson sampling by default. Set thompson=False for the
    epsilon-greedy version, which is kept because the comparison is the point:
    blind exploration measurably degrades an agent whose priors were already
    correctly ordered.
    """
    rng = random.Random(seed)
    beliefs = starting_beliefs(world)
    L = Learner(beliefs=beliefs)

    causes = [c for c in PLAYBOOKS if PLAYBOOKS[c].candidates and c not in FROZEN]

    # Round 0 is the state BEFORE any observation. An earlier version recorded
    # the first entry after a full round of 220 draws - about 10 per
    # (cause, action) - and orderings flip on noise at n=10, so the reported
    # starting score was an artefact of the measurement, not the priors.
    history = [{"round": 0, "rate": 0.0,
                "err": belief_error(L, world), "top1": top1_agreement(L, world)}]

    for r in range(rounds):
        eff = L.learned_efficacy(fallback=beliefs)
        recovered = attempted = 0
        for _ in range(per_round):
            cause = rng.choice(causes)
            cands = [a for a in PLAYBOOKS[cause].candidates
                     if a in TRUE_EFFICACY.get(cause, {})]
            if not cands:
                continue
            if thompson:
                action = max(cands, key=lambda a: L.sample(cause, a, rng))
            elif rng.random() < 0.12:
                action = rng.choice(cands)
            else:
                action = max(cands, key=lambda a: eff.get(cause, {}).get(a, 0.0))
            ok = rng.random() < true_p(cause, action)
            L.observe(cause, action, ok)
            recovered += ok
            attempted += 1
        history.append({
            "round": r + 1,
            "rate": recovered / attempted if attempted else 0,
            "err": belief_error(L, world),
            "top1": top1_agreement(L, world),
        })
    return L, history


def belief_error(L, world):
    """Mean absolute gap between what we believe and what is true."""
    diffs = []
    for cause, row in TRUE_EFFICACY.items():
        if cause in FROZEN:
            continue
        for action in PLAYBOOKS[cause].candidates:
            if action not in row:
                continue
            diffs.append(abs(L.mean(cause, action) - true_p(cause, action)))
    return sum(diffs) / len(diffs) if diffs else 0.0


def top1_agreement(L, world):
    """How often our best-believed action IS the truly best one."""
    hits = tot = 0
    for cause in PLAYBOOKS:
        if cause in FROZEN:
            continue
        cands = [a for a in PLAYBOOKS[cause].candidates
                 if a in TRUE_EFFICACY.get(cause, {})]
        if len(cands) < 2:
            continue
        believed = max(cands, key=lambda a: L.mean(cause, a))
        actual = max(cands, key=lambda a: true_p(cause, a))
        hits += believed == actual
        tot += 1
    return hits / tot if tot else 0.0


# ──────────────────────────────────────────────────────────── invariants

def check_invariants(L):
    """These are asserted, not hoped for."""
    problems = []
    eff = L.learned_efficacy()

    for cause, row in eff.items():
        allowed = set(PLAYBOOKS[cause].candidates)
        for action in row:
            if action not in allowed:
                problems.append(f"learned an action outside the {cause.value} playbook")
        if PLAYBOOKS[cause].retry_forbidden:
            for a in (Action.RETRY_NOW, Action.RETRY_SCHEDULED):
                if a in row:
                    problems.append(
                        f"{cause.value} forbids retry but the learner surfaced {a.value}")

    for cause in FROZEN:
        for action, p in eff.get(cause, {}).items():
            if p != 0.0:
                problems.append(f"{cause.value} moved off zero to {p:.3f}")
    return problems


# ──────────────────────────────────────────────────────────── reporting

def spark(vals, width=44):
    ch = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    if not vals:
        return ""
    step = max(1, len(vals) // width)
    s = vals[::step][:width]
    lo, hi = min(s), max(s)
    if hi - lo < 1e-9:
        return ch[3] * len(s)
    return "".join(ch[min(7, int((v - lo) / (hi - lo) * 7.99))] for v in s)


def window(hist, key, k=5, end=False):
    """
    Mean over the first or last k rounds - one round is mostly noise.

    Round 0 holds the pre-data state and has no recovery rate, so it is
    excluded from rate windows but IS the honest starting point for belief
    quality.
    """
    src = hist if key != "rate" else [h for h in hist if h["round"] > 0]
    sl = src[-k:] if end else src[:k]
    return sum(h[key] for h in sl) / len(sl)


def at_start(hist, key):
    """The pre-data value - what the hand-written priors were actually worth."""
    return hist[0][key]


def report(world, L, hist, rounds, per_round):
    first, last = hist[0], hist[-1]
    r0, r1 = window(hist, "rate"), window(hist, "rate", end=True)
    t0, t1 = at_start(hist, "top1"), window(hist, "top1", end=True)
    e0, e1 = at_start(hist, "err"), window(hist, "err", end=True)
    print(BAR)
    print(f"  LEARNING THE PRIORS \u2014 {world} world")
    print(f"  {rounds} rounds \u00d7 {per_round} attempts = {rounds*per_round:,} observations")
    print(BAR)

    if world == "inverted":
        print("\n  The agent starts believing the WORST action for each cause is")
        print("  the best one. Nothing tells it that it is wrong.")

    print("\n  Belief quality is read before any data and after the last 5")
    print(f"  rounds. A single round of {per_round} attempts carries about")
    print("  +/-2pp of sampling noise, so rates are 5-round means.\n")
    print(f"  picks the best action {t0:6.1%} \u2192 {t1:6.1%}   "
          f"{spark([h['top1'] for h in hist])}")
    print(f"  belief error          {e0:6.3f} \u2192 {e1:6.3f}   "
          f"{spark([-h['err'] for h in hist])}")
    print(f"  recovery rate         {r0:6.1%} \u2192 {r1:6.1%}   "
          f"{spark([h['rate'] for h in hist])}")
    print("\n  Best-action agreement is the metric that matters. Recovery rate")
    print("  moves with the random mix of causes drawn each round, so it is the")
    print("  noisiest of the three and the least worth reading alone.")

    print("\n\n  WHAT IT LEARNED")
    print(f"    {'cause':<26}{'best action':<20}{'learned':>9}{'true':>8}{'n':>7}")
    print("    " + "-" * 70)
    for cause in sorted(PLAYBOOKS, key=lambda c: c.value):
        if cause in FROZEN:
            continue
        cands = [a for a in PLAYBOOKS[cause].candidates
                 if a in TRUE_EFFICACY.get(cause, {})]
        if not cands:
            continue
        best = max(cands, key=lambda a: L.mean(cause, a))
        truly = max(cands, key=lambda a: true_p(cause, a, world))
        mark = " " if best == truly else "  \u2717 still wrong"
        print(f"    {lang.cause_name(cause.value):<26}"
              f"{lang.action_name(best.value):<20}"
              f"{L.mean(cause,best):>9.2f}"
              f"{true_p(cause,best):>8.2f}"
              f"{L.trials(cause,best):>7}{mark}")

    print("\n\n  WHAT IT REFUSED TO LEARN")
    probs = check_invariants(L)
    print(f"\n    actions invented outside a playbook      {'0  \u2713' if not probs else len(probs)}")
    print(f"    forbidden retries resurrected            "
          f"{'0  \u2713' if not any('retry' in p for p in probs) else 'FAIL'}")
    print(f"    risk declines moved off zero             "
          f"{'0  \u2713' if not any('zero' in p for p in probs) else 'FAIL'}")
    for p in probs:
        print(f"      \u2717 {p}")
    print("\n    Learning reorders the options inside a playbook. It cannot add")
    print("    one, and it cannot revive one the taxonomy forbids. Evidence that")
    print("    'retrying dead cards sometimes works' never becomes a retry.")

    print("\n\n  WHERE IT IS STILL UNSURE")
    thin = []
    for cause in PLAYBOOKS:
        if cause in FROZEN:
            continue
        for a in PLAYBOOKS[cause].candidates:
            if a in TRUE_EFFICACY.get(cause, {}) and L.trials(cause, a) < MIN_TRIALS:
                thin.append((L.trials(cause, a), cause, a))
    if not thin:
        print(f"\n    Every pair cleared {MIN_TRIALS} observations.")
    else:
        print(f"\n    {len(thin)} pairs under {MIN_TRIALS} observations - these keep the")
        print("    hand-written prior rather than a noisy estimate:")
        for n, c, a in sorted(thin)[:5]:
            print(f"      {lang.cause_name(c.value):<26}"
                  f"{lang.action_name(a.value):<20} n={n}")
    print("\n" + BAR)
    return probs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", choices=["aligned", "inverted"], default="aligned")
    ap.add_argument("--rounds", type=int, default=60)
    ap.add_argument("--per-round", type=int, default=220)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--greedy", action="store_true",
                    help="use epsilon-greedy instead of Thompson sampling")
    ap.add_argument("--both", action="store_true",
                    help="run both worlds and compare recovery")
    a = ap.parse_args()

    worlds = ["aligned", "inverted"] if a.both else [a.world]
    results = {}
    for w in worlds:
        print()
        L, hist = simulate(w, a.rounds, a.per_round, a.seed,
                           thompson=not a.greedy)
        probs = report(w, L, hist, a.rounds, a.per_round)
        results[w] = (hist, probs)

    if a.both:
        print("\n" + BAR)
        print("  DOES IT RECOVER FROM BEING WRONG?")
        print(BAR)
        print(f"\n    {'world':<12}{'start':>10}{'end':>10}{'best-action agreement':>26}")
        print("    " + "-" * 58)
        for w in worlds:
            h = results[w][0]
            print(f"    {w:<12}{window(h,'rate'):>9.1%}{window(h,'rate',end=True):>10.1%}"
                  f"{at_start(h,'top1'):>16.0%} \u2192 {window(h,'top1',end=True):.0%}")
        inv = results["inverted"][0]
        ali = results["aligned"][0]
        print(f"\n    The hand-written priors order actions correctly to begin with")
        print(f"    ({at_start(ali,'top1'):.0%}). What they get wrong is magnitude, not ranking -")
        print("    the agent believes actions work better than they do, which")
        print("    inflates every expected value it computes.")
        print(f"\n    The inverted agent starts at {at_start(inv,'top1'):.0%} - every preference backwards -")
        print(f"    and climbs to {window(inv,'top1',end=True):.0%} on outcomes alone. Nothing corrected it, and")
        print("    it never stepped outside a playbook to get there.")
        print("\n" + BAR)

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
