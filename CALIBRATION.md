# Calibration and honest limits

Any simulator can be tuned until it produces a triumphant number. This document
exists so a reviewer can find the places where that could have happened, without
having to hunt.

## Run it yourself

    python3 calibrate.py --seeds 20 --n 600

## What the harness reports

**Variance across seeds.** Same world, different random draws. On a 400-event
cohort across 12 seeds:

| Measure | Mean | Range | SD |
|---|---|---|---|
| lift over holdout | 27.6 pp | 10.6 – 37.5 | 6.8 |
| holdout recovery rate | 21.8% | 14.8 – 35.1 | 6.1 |
| exception rate | 3.4% | 2.5 – 4.9 | 0.7 |

The claim is a lift of **28 pp ± 7 on this cohort**, not a single run. The
spread is real and we report it rather than picking seed 7 and calling it a day.

**Sensitivity to the invented world.** `EFFICACY_SCALE` in `generator.py` scales
the true probability that any intervention works. Sweeping it:

| World efficacy | Holdout | Worked | Lift | Incremental |
|---|---|---|---|---|
| 0.20 | 22.7% | 37.2% | 14.5 pp | ₹58,812 |
| 0.30 | 21.4% | 41.8% | 20.4 pp | ₹92,194 |
| **0.42 (shipped)** | 21.1% | 50.5% | 29.4 pp | ₹129,785 |
| 0.60 | 23.5% | 58.0% | 34.5 pp | ₹173,336 |
| 0.80 | 24.6% | 65.2% | 40.6 pp | ₹223,092 |

Two things to read off this table. The lift moves monotonically with the world's
true efficacy, in the direction and magnitude you would expect — the agent is
not manufacturing lift out of nothing. And the **holdout rate stays roughly flat
across every scale**, which is the evidence that the control arm is genuinely
independent of the agent and therefore a valid counterfactual.

## Where the priors came from, and where they are guesses

| Parameter | Basis | Confidence |
|---|---|---|
| Failure reason mix | shape of a mid-market Indian merchant's book: UPI-dominant, rail wobble and auth friction as the two largest buckets | moderate — the ordering is defensible, the exact weights are ours |
| Organic recovery 7–29% by cause | customers who retry unaided; higher for transient failures, near zero for dead instruments and risk declines | moderate |
| `EFFICACY_SCALE = 0.42` | chosen so overall recovery lands in a range consistent with published smart-retry performance rather than the ~70% an untuned simulator produces | **this is a judgement call, and it is the single most load-bearing number in the repo** |
| Action costs (₹0.18 SMS, ₹0.80 WhatsApp, ₹2.60 voice, ₹45 human) | approximate Indian per-message and per-agent-minute rates | good |
| Goodwill costs | entirely ours. There is no market price for annoying a customer, so we invented one and put it in the same equation as everything else, where it can be argued with | **openly a construct** |

## What this project does not claim

- **Not** that a real merchant would see a 28 pp lift. The absolute number is a
  property of a world we invented and it moves with the table above.
- **Not** that the model is doing the heavy lifting. Rules classify ~93% of
  events. The model handles the residue and writes copy.
- **Not** that these are real payments. Razorpay test mode, and a mock transport
  when no keys are present so the repo runs for anyone who clones it.

## What it does claim

The architecture, the ordering of gates before scoring, the refusal to retry
around a risk decline, the graceful degradation when the gateway dies, the
validation cage around model output, and the holdout methodology. Those are
real, they are inspectable, and they would transfer unchanged to live data.

The number is a demonstration. The method is the submission.
