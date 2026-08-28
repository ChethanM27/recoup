# Architecture

Recoup is a control tower for revenue that is slipping away. It ingests at-risk
events, diagnoses each one, decides whether to act, acts through Razorpay, and
measures what that was worth against a control group.

```
  at-risk events                 ┌──────────────────────────────────────┐
  (failed payments,              │  1. DIAGNOSIS                        │
   abandoned checkouts,   ─────► │  deterministic rules  ~93%           │
   failed mandates)              │  language model       ~7% (residue)  │
                                 └──────────────┬───────────────────────┘
                                                │ root cause + confidence
                                 ┌──────────────▼───────────────────────┐
                                 │  2. PLAYBOOK                         │
                                 │  8 causes, each with its own ordered │
                                 │  candidate actions and attempt cap   │
                                 └──────────────┬───────────────────────┘
                                                │ candidate actions
                                 ┌──────────────▼───────────────────────┐
                                 │  3. HARD GATES  (deterministic)      │
                                 │  kill switch · confidence floor      │
                                 │  risk-decline-is-final · attempt cap │
                                 │  opt-out · quiet hours · cooldown    │
                                 │  touch cap · consent · budget · floor│
                                 └──────────────┬───────────────────────┘
                                    survivors   │   blocked → logged, not dropped
                                 ┌──────────────▼───────────────────────┐
                                 │  4. EXPECTED VALUE                   │
                                 │  p(success)·amount − cost − goodwill │
                                 │  below threshold → deliberate no-op  │
                                 └──────────────┬───────────────────────┘
                                                │ one chosen action
                                 ┌──────────────▼───────────────────────┐
                                 │  5. EXECUTION                        │
                                 │  Razorpay test mode: payment links   │
                                 │  retry · backoff · circuit breaker   │
                                 │  unreachable → queued with a reason  │
                                 │  copy: LLM → validator → send        │
                                 └──────────────┬───────────────────────┘
                                                │
                                 ┌──────────────▼───────────────────────┐
                                 │  6. AUDIT + MEASUREMENT              │
                                 │  every decision, every gate, every   │
                                 │  rejection → SQLite                  │
                                 │  20% holdout → incremental lift      │
                                 └──────────────────────────────────────┘
```

## Where the language model sits, and where it does not

The model has two jobs and no others.

**Diagnosis of the residue.** Deterministic rules classify the overwhelming
majority of failures from the error signature. Where a rule fires, the rule wins
and no model call is made — spending a call to re-derive `card_expired →
INSTRUMENT_DEAD` is latency, not intelligence. The model is asked only about
signatures no rule matched. Its confidence is capped below rule-level certainty
so it can never outrank a rule, and it is explicitly instructed that an honest
`UNKNOWN` routing to a human beats a confident wrong answer that contacts a real
customer.

**Customer-facing copy.** Hinglish outreach tuned to the root cause.

Everything it returns is untrusted input:

| Model output | Validation before it can matter |
|---|---|
| root cause | must be one of eight known causes, else rejected |
| confidence | clamped to [0, 0.85] so a rule always wins |
| suggested action | must already exist in that cause's playbook, then still runs every gate |
| message copy | length limit, no threat vocabulary, no invented amounts or deadlines, opt-out line required, `{link}` placeholder required |

Rejections fall back to a deterministic template and are logged with the reason.
The model gets a vote. It never gets a veto, and it never gets the last word.

## Why gates run before expected value

Ordering is the design. If expected value ran first, a large enough ticket would
always be able to justify overriding a rule — and the single most dangerous
failure mode of a money agent is one that can talk itself past a compliance
control because the payoff looked good. Gates are boolean, evaluated first, and
not reachable by any scoring path.

The hardest of them: **a risk decline is final.** An agent that retries around a
risk or compliance block is an agent laundering a decline. That gate is not
overridable by expected value, by ticket size, or by the model.

## Failure handling

`demo_failure.py` is the drill. Razorpay goes dark mid-batch:

1. calls retry with exponential backoff
2. after 4 consecutive failures the circuit opens and we **stop calling entirely**
3. subsequent intents are queued with a reason, never silently dropped
4. the batch runs to completion and reports honestly
5. cooldown expires, one probe goes through, circuit closes, queue drains

A recovery agent that dies when the gateway hiccups is worse than none at all,
because the merchant now believes the money is being chased.

## Measurement

20% of the book is randomly held out and never touched. Reported recovery is
treatment rate minus control rate. Customers in the worked arm who pay without
our help are counted as organic, not as our win.

The generator gives every event latent ground truth — how likely that specific
customer is to pay under each intervention, and whether they would have paid
unaided. The decision engine never reads those fields, and its own success
priors are deliberately *not* the true rates. A real agent decides under
uncertainty; so does this one.

## Module map

| File | Job |
|---|---|
| `recoup/taxonomy.py` | eight root causes, playbooks, action costs, classifier |
| `recoup/policy.py` | hard gates, expected-value scoring, customer state |
| `recoup/engine.py` | batch runner, holdout split, audit trail, metrics |
| `recoup/generator.py` | synthetic cohort with latent ground truth |
| `recoup/razorpay_client.py` | test-mode API, retry, circuit breaker, queue |
| `recoup/llm.py` | provider-agnostic model layer and the validation cage |
| `serve.py` + `static/` | dashboard over the audit trail |
| `calibrate.py` | variance across seeds, sensitivity to the world |
| `demo_failure.py` | the graceful-failure drill |
