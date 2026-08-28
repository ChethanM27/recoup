# Recoup

[![verify](https://github.com/ChethanM27/recoup/actions/workflows/verify.yml/badge.svg)](https://github.com/ChethanM27/recoup/actions/workflows/verify.yml)

**A revenue recovery control tower.**
Razorpay AI Buildathon · Track 03 — AI Revenue Recovery

Recoup takes a merchant's book of at-risk revenue — failed payments, abandoned
checkouts, failed subscription mandates — diagnoses the root cause of each one,
decides whether acting is worth it, executes a bounded recovery workflow through
Razorpay, and measures the result against a control group it never touched.

Retrying isn't recovery. Diagnosing is.

---

## If you only read one thing

Two results, both reproducible from a clean clone in under a minute.

**The classifier is scored against ground truth it never sees.** `generator.py`
corrupts 14% of gateway codes before the engine looks at them — half go generic,
half go *wrong but plausible*. Accuracy is reported split by slice, because a
blended number would flatter us:

| what the gateway reported | accuracy | what we do |
|---|---|---|
| the truth | ~99% | act |
| a generic code | ~29% | abstain — a human takes it |
| a wrong but valid code | **0%** | act, confidently, and be wrong |

Be precise about that last row: it is **0% by construction, not by
measurement.** The corrupted codes are drawn from other causes' code sets, so a
lookup classifier cannot recover them even in principle. It is a statement about
the ceiling of reading decline codes — when an issuer masks a dead card behind
`insufficient_funds`, nothing in the payload can tell you — not an empirical
score this classifier happened to earn. About 59% of all errors get caught by the
confidence floor before anything happens. The rest is quantified, in rupees, in
`evaluate.py` and on the dashboard.

**The result survives its own beliefs being wrong.** `stress.py` rewrites the
world underneath the agent — scrambling and then inverting which actions
actually work — without telling it:

| world | lift | forbidden retries |
|---|---|---|
| aligned | 25.5pp | 0 |
| scrambled | 15.0pp | 0 |
| inverted | 11.0pp | 0 |

The claimed number degrades toward zero, which is what an honest measurement
does when the beliefs behind it are wrong. The guardrails do not move, because
they run *before* the scoring rather than after it. A wrong belief can make this
agent ineffective. It cannot make it non-compliant.

---
    python3 demo_failure.py                       # the gateway-failure drill
    python3 calibrate.py                          # variance + sensitivity

No dependencies. No build step. Standard library only. It runs **without any API
keys** — the Razorpay client falls back to a mock transport and the model layer
falls back to deterministic templates, so a reviewer can clone and run it in
fifteen seconds.

To run against real Razorpay test mode and a live model, copy `.env.example` to
`.env` and fill it in.

---
---

## Run it

No install step. No dependencies. Python 3.9+ and nothing else.

    python3 serve.py                  # dashboard at localhost:8000  ← start here
    python3 run_sim.py --n 600        # the same batch, as a terminal report
    python3 evaluate.py --n 2000      # is the diagnosis actually any good?
    python3 stress.py --n 800         # break the agent's beliefs, see what survives
    python3 redteam.py --verbose      # attack the LLM cage with what models get wrong
    python3 learn.py --both           # learn the priors instead of hand-writing them
    python3 demo_failure.py           # gateway dies mid-batch

---
---

## Three claims

### 1. Root-cause routing, not blanket retries

An expired card and a timed-out bank gateway are not the same problem. Recoup
sorts every failure into one of eight causes from its Razorpay error signature,
and each cause gets its own playbook:

| Root cause | What it means | What we do |
|---|---|---|
| `TRANSIENT_RAIL` | gateway or issuer wobbled | retry quietly with backoff |
| `INSUFFICIENT_FUNDS` | account was short | wait for money to land, then retry |
| `AUTH_FRICTION` | stuck at OTP/3DS, high intent | switch to a lower-friction rail now |
| `INSTRUMENT_DEAD` | card expired, VPA invalid | **retry forbidden** — collect a new instrument |
| `RISK_BLOCKED` | risk or compliance declined | **hard stop**, route to a human |
| `CUSTOMER_ABANDON` | walked away | one nudge carrying the cart, then leave them alone |
| `MANDATE_FAILURE` | e-mandate presentation failed | space attempts to scheme rules |
| `UNKNOWN` | can't tell | exception list, stays in the denominator |

Retry is one of nine actions, and it is **forbidden outright for three of the
eight causes**. Retrying an expired card three times isn't recovery, it's spam.

### 2. Nothing fires without clearing every gate

Kill switch · confidence floor · risk-decline-is-final · attempt cap · opt-out ·
quiet hours · cooldown · per-customer touch cap · channel consent · ticket floor
· daily budget · expected-value floor.

Gates are boolean and they run **before** expected-value scoring, never after.
The single most dangerous failure mode of a money agent is one that talks itself
past a compliance control because the payoff looked good. Here there is no
scoring path that reaches a gate.

The audit trail records what we chose *not* to do, not just what we did — every
gate evaluation, pass or fail, with its reasoning.

### 3. The headline number is incremental

20% of the book is randomly held out and never touched. Reported recovery is
treatment rate minus control rate.

On a 600-event batch:

```
  what a naive dashboard reports      ₹262,252
  what Recoup actually recovered      ₹109,708      +28.7 pp over holdout
  recovery spend                        ₹1,113
  unresolved, sent to a human             3.4%
```

The gap is the ~21% of customers who pay us with no help at all. Every recovery
tool without a holdout is quietly taking credit for them.

Across 20 seeds: **28 pp ± 7.** See [CALIBRATION.md](CALIBRATION.md) for the
variance table, the sensitivity sweep, and a plain list of every prior that is a
judgement call rather than a measurement.

---
---

## Watch it think

The dashboard's centrepiece. Pick a payment, press play, and it walks the whole
decision in seven steps: the failure, the diagnosis and how sure we were, the
options the playbook allowed, **every guardrail evaluating one at a time** with
the chain snapping where one fails, the expected-value maths, the message that
actually went out, and the outcome.

The interesting story is rarely the action that succeeded. It's the one we
wanted to take and the rule that stopped us — so when a guardrail blocks the
best option, the walkthrough shows *that* ladder breaking, then what we fell
back to instead.

Every internal name has a plain-English one. `RISK_BLOCKED` reads as "Risk said
no". `touch_frequency_cap` reads as "We haven't messaged them too much lately".
The engine stays strict; the interface stays readable.

---
---

## Where the AI is

A language model reads the declines rules can't parse, and writes the customer
message. It is never trusted. `redteam.py` attacks the validator with nine real
failure modes of instruction-tuned models — inventing an amount, drifting into
legal threats, claiming a failed card hits your credit score, dropping the
opt-out, proposing a retry against a compliance decline — and every one is
refused. **17/17 checks pass, 13 attacks blocked**, recomputed live on the
dashboard against the same validators the engine uses.

Model confidence is capped at **0.85** where a deterministic code match earns
**0.97**, so a confident hallucination can never outrank a rule or clear a gate
a rule would have failed.

The argument for putting a model in this loop isn't that it behaves. It's that
it doesn't have to.

---

Two jobs: diagnosing the ~7% of failures no rule matched, and writing the
Hinglish outreach copy. Where a rule fires, the rule wins and no call is made.

Everything the model returns is untrusted input. An invented cause is rejected.
Its confidence is capped below rule-level certainty so a rule always outranks it.
A suggested action must already exist in that cause's playbook and then still
clears every gate. Copy is validated for length, threat vocabulary, invented
amounts and a required opt-out line — and falls back to a template, with the
reason logged, if it fails.

The model gets a vote. It never gets a veto.

---
---

## The priors should be learned, not written

`policy.BELIEVED_EFFICACY` is 56 hand-written probabilities, and CALIBRATION.md
already calls that the weakest thing here. `learn.py` replaces them with a
Beta-Bernoulli posterior per (cause, action), updated from outcomes.

Selection is Thompson sampling — the posterior is *sampled*, not read, so
exploration is proportional to remaining uncertainty. Over 13,200 observations:

| starting beliefs | picks the truly best action |
|---|---|
| my hand-written priors | 100% → **100%** |
| every preference **inverted** | 0% → **83%** |

The first row is the honest one: my priors already order the actions correctly,
so learning cannot improve them. What it does is *not damage them* — an earlier
epsilon-greedy version degraded that row to 83%, because blind exploration keeps
spending attempts on options it already knows are bad. Run `--greedy` to see it.

The second row is why the file exists. An agent that starts with every
preference backwards climbs to 83% on outcomes alone, with nothing correcting
it.

**Learning here is insurance, not an upgrade.** If the priors are right it costs
nothing; if they are wrong it recovers. Given that the priors are hand-written,
that is the property worth having.

What the priors *do* get wrong is magnitude. `BELIEF_SCALE` is 0.50 against a
true `EFFICACY_SCALE` of 0.42 — the agent is systematically **19% optimistic**,
so every expected value it computes is inflated. That is deliberate and
documented rather than tuned away: a deployed agent never knows the true scale,
and the guardrails have to hold while it is wrong. `stress.py` tests a much
worse case than 19%.

Throughout, learning may only reorder actions *inside* a playbook. It cannot
add one, cannot revive one the taxonomy forbids, and `RISK_BLOCKED` stays
pinned at zero no matter what the data says. Those three invariants are
asserted on every run, not hoped for.

---
---

## When Razorpay stops answering

`demo_failure.py` runs the drill. Gateway goes dark mid-batch:

```
   1  executed   circuit=closed     https://rzp.io/i/D0001
   3  QUEUED     circuit=closed     gateway_unreachable
   6  QUEUED     circuit=open       gateway_unreachable      ← breaker trips
   7  QUEUED     circuit=open       circuit_open             ← wire untouched
  15  executed   circuit=closed     https://rzp.io/i/D0007   ← probe, recovered

  intents silently dropped       0
  calls made while circuit open  0
```

Retry with backoff, circuit breaker, queue-with-reason, then drain on recovery.
The batch never crashes, and the merchant is never told money is being chased
when it isn't.

---
---

## Docs

- `RECORDING.md` — shot-by-shot plan for the demo video

- [ARCHITECTURE.md](ARCHITECTURE.md) — the full pipeline and every design decision
- [CALIBRATION.md](CALIBRATION.md) — variance, sensitivity, and what this does **not** claim
- [SUBMISSION.md](SUBMISSION.md) — brief coverage and panel Q&A
---

## Honesty notes

The cohort is synthetic. Each event carries latent ground truth — how likely that
customer is to pay under each intervention, and whether they'd have paid unaided.
The decision engine never reads those fields, and its own priors are deliberately
*not* the true rates: a real agent decides under uncertainty, so this one does
too.

The number is a demonstration. The method is the submission.
