# Submission checklist

Track 03 — AI Revenue Recovery. Deadline 5 September.

## What the brief asks for, and where it is

> "Build an agent that detects revenue at risk, determines the right
> intervention, and executes a bounded recovery workflow."

| Requirement | Where |
|---|---|
| Detects revenue at risk | `recoup/generator.py` → failed payments, abandonment, mandate failures |
| Determines the right intervention | `recoup/taxonomy.py` → 8 root causes, 8 playbooks |
| Executes a bounded workflow | `recoup/policy.py` gates + `recoup/razorpay_client.py` |
| **Measured money recovered across a batch** | `run_sim.py`, holdout-adjusted |
| **Compliant escalation** | risk declines and unknowns route to a human |
| **Stopping rules** | attempt caps, touch caps, cooldown, quiet hours, budget, EV floor, kill switch |
| **Audit trail** | SQLite, every gate evaluation, viewable in the dashboard |
| One failure handled gracefully | `demo_failure.py` |
| **Diagnosis scored against ground truth** | `evaluate.py`, live on the dashboard |
| **Result tested against its own assumptions** | `stress.py` |
| **LLM outputs validated, and the validator tested** | `recoup/llm.py`, `redteam.py` |
| **Priors learned from outcomes, not hand-written** | `learn.py` |

## Before you submit

- [ ] Razorpay test key regenerated; old one dead
- [ ] `.env` exists locally and is **not** in the repo (`git status` must not show it)
- [ ] `git log` contains no secrets in any commit, including old ones
- [ ] Repo is **public**
- [ ] `python3 run_sim.py` works on a fresh clone with no `.env` at all
- [ ] `python3 serve.py` works and the dashboard renders
- [ ] `python3 demo_failure.py` works
- [ ] `python3 calibrate.py` works
- [ ] `python3 evaluate.py` works
- [ ] `python3 stress.py` works
- [ ] `python3 redteam.py` reports 17/17
- [ ] `python3 learn.py --both` works
- [ ] Video uploaded unlisted to YouTube, link tested in a private window
- [ ] Form submitted at https://forms.gle/d9r2gvxp8cmoZhon9

## Pushing to GitHub

    cd recoup
    git init
    git add .
    git commit -m "Recoup: revenue recovery control tower"
    git branch -M main
    git remote add origin https://github.com/<your-username>/recoup.git
    git push -u origin main

`.gitignore` already excludes `.env`, `*.db` and `__pycache__`. Run
`git status` before the first commit and confirm `.env` is not listed.

## Questions a panel will ask, and the honest answer

**"Why is your recovery number lower than everyone else's?"**
Because 20% of the book is held out and never touched, and I subtract it. The
others are counting customers who would have paid anyway.

**"Isn't this just retry logic with extra steps?"**
Retry is one of nine actions, and it's forbidden outright for three of the eight
root causes. Retrying an expired card or a risk decline is the failure mode I
built this to prevent.

**"How much of this is the LLM?"**
Roughly 7% of diagnoses plus the copy. Rules do the rest. I'd rather have a
deterministic system with a model at the edges than a model with rules bolted on.

**"Your data is synthetic. Why should I believe the number?"**
You shouldn't believe the number, and `CALIBRATION.md` says so explicitly. It
shows variance across seeds and a sensitivity sweep over the parameter that
drives it. What transfers to live data is the architecture, the gate ordering,
and the holdout methodology.

**"What breaks first at real scale?"**
The success priors in `policy.py` are hand-set. In production they'd be learned
per merchant, per rail, per cause — the holdout arm is already there to train
them honestly. That's the next build, and it's the reason the control group
matters beyond just reporting.
