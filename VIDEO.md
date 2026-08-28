# The 5-minute pitch video

Chetan — this is yours to perform. Read it in your own voice, don't recite it.
Every claim below is one the repo can actually back, so you can be asked about
any of it.

**Setup before you record**
- Two terminal windows, font size cranked up, dark theme.
- Browser at `localhost:8000` with the dashboard already loaded (never load it
  live on camera).
- Screen recording + your webcam in a corner. OBS is free.
- Do the whole thing in one take if you can. Small stumbles read as human;
  over-editing reads as a corporate ad.

---

## 0:00 – 0:30 · The problem, stated as money

> Face on camera.

"A payment fails. A checkout gets abandoned. A subscription mandate doesn't go
through. Each one is small, and each one is somebody's revenue quietly leaving.

Most tools stop at telling you it happened. And most 'recovery' tools do one
thing: retry. Retry the card that expired. Retry the payment the risk engine
declined. Retry until the customer blocks you.

I built Recoup, because retrying isn't recovery. Diagnosing is."

---

## 0:30 – 1:15 · Why one retry rule is the wrong shape

> Screen: `recoup/taxonomy.py`, scroll slowly through the eight causes.

"Every failed payment has a cause, and the causes need opposite treatments.

A gateway timeout — customer did nothing wrong, they don't even know it failed.
Retry quietly.

Insufficient funds — retrying in twenty minutes just burns another decline. Wait
for the money to land.

Expired card — retrying is *guaranteed* to fail. It isn't recovery, it's spam.
The only path is collecting a new instrument.

And a risk decline — an agent that retries around a risk block is an agent
laundering a decline. Hard stop. Human.

Eight causes, eight playbooks. That's the first thing Recoup does."

---

## 1:15 – 2:15 · The gate ladder — the heart of it

> Screen: dashboard. Click a decision in the stream. Let the ladder render.

"Here's a real decision. Fifteen thousand rupees at risk, authentication
friction — this customer wanted to pay and got stuck at OTP.

Every gate the decision had to clear, in the order it ran. Kill switch.
Confidence floor. Is this a risk decline. Attempt cap. Has the customer opted
out. Quiet hours — we don't WhatsApp anyone at 2am. Cooldown. Touch cap. Daily
budget.

> Click one that was blocked.

And this one *stopped*. The ladder breaks right there, and it tells you which
gate and why.

That ordering is deliberate. Gates run *before* the expected-value calculation,
not after. Because the most dangerous thing a money agent can do is talk itself
past a compliance control when the payoff looks big enough. Here it can't — the
gates are boolean, they run first, and there's no scoring path that reaches
them."

---

## 2:15 – 3:00 · The language model, and its cage

> Screen: `recoup/llm.py`, the validation section.

"The model does two jobs. It diagnoses the failures my rules couldn't classify —
about seven percent — and it writes the customer message in Hinglish.

Everything it returns is untrusted. A cause it invents gets rejected. Its
confidence is capped below rule-level certainty, so a rule always wins. An action
it suggests has to already exist in that cause's playbook, and then it still runs
every single gate.

And the copy gets validated: no legal threats, no CIBIL, no 'final warning', no
invented amounts, opt-out line required. If it fails validation, we fall back to
a template and log why.

The model gets a vote. It never gets a veto."

---

## 3:00 – 3:45 · When Razorpay stops answering

> Screen: `python3 demo_failure.py`. Let it run live.

"The brief asks for one failure handled gracefully. So — the gateway goes dark
mid-batch.

Calls fail. Retry with backoff. Four consecutive failures, and the circuit
breaker opens — and now watch: zero calls. We stop touching the wire entirely
instead of hammering something that's already down.

Every recovery intent gets queued *with a reason*. Nothing is dropped.

Gateway comes back, cooldown expires, one probe goes through, circuit closes,
queue drains.

The batch never crashed. And the merchant is never told money is being chased
when it isn't."

---

## 3:45 – 4:40 · The number, and why it's smaller than everyone else's

> Screen: top of the dashboard. Let both numbers sit on screen.

"Now the part I care about most.

A naive dashboard on this batch reports two-sixty-two thousand recovered. Recoup
reports one-oh-nine.

Mine is smaller because mine is honest. Twenty percent of the book is randomly
held out and never touched. Some of those customers pay anyway — about twenty-one
percent do. Every recovery tool that doesn't hold out a control group is quietly
taking credit for those people.

So the number I'll defend is the *difference*. Twenty-eight percentage points of
incremental lift.

> Screen: `python3 calibrate.py`

And not from one lucky run. Twenty seeds — twenty-eight points, plus or minus
seven. Plus a sensitivity sweep showing exactly how much of the result comes from
the world I simulated versus the agent, because that's the question I'd ask.

Three and a half percent I couldn't resolve at all. They're on the exception
list, they went to a human, and they're still in the denominator of every metric
on this page."

---

## 4:40 – 5:00 · Close

> Face on camera.

"Recoup diagnoses before it acts, refuses before it spends, measures against a
control group, and degrades gracefully when the gateway dies.

Repo's linked. It runs with `python3 serve.py` — no dependencies, no build step,
and it works without API keys so you can just clone it and look.

The number is a demonstration. The method is what I'm submitting."

---

## Storyboard at a glance

| Time | Screen | One thing to land |
|---|---|---|
| 0:00 | face | revenue leaks in small pieces |
| 0:30 | taxonomy.py | eight causes need opposite treatments |
| 1:15 | gate ladder | gates run before scoring, and one breaks the chain |
| 2:15 | llm.py | the model gets a vote, not a veto |
| 3:00 | demo_failure.py live | zero calls while the circuit is open |
| 3:45 | dashboard hero | my number is smaller because it's honest |
| 4:20 | calibrate.py | ±7 across 20 seeds, plus sensitivity |
| 4:40 | face | the method is the submission |

## Delivery notes

- **Slow down on the gate ladder and the two numbers.** Those are the two things
  a judge will remember. Everything else can be brisk.
- **Say "I built" and "I decided", not "the system does".** Ownership.
- **Don't apologise for it being synthetic data.** Say it plainly, once, and move
  on — you've got `CALIBRATION.md` covering exactly that, which is more than most
  submissions will have.
- If you fluff a line, pause fully, and start that sentence again. Easy cut.
