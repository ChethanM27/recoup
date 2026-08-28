# Recording plan

You record this. I can't generate video, and it would be the wrong call anyway —
a buildathon panel can tell when a builder didn't present their own work, and
the person who can answer "why did you do it that way" is you.

What follows removes every decision from the process. Exact commands, exact
timings, exact words. Read the narration off the screen if you want.

---

## Before you start

Two terminal windows, both in the project folder.

**Terminal A** — leave this running the whole time:

```
python serve.py --n 500
```

**Terminal B** — you'll type into this on camera.

Browser at `localhost:8000`. Zoom to **125%** so text is readable when the
video gets compressed. Close every other tab. Turn off notifications.

Record at **1080p**. OBS Studio is free and fine. On Windows, `Win+G` also
works. Record system audio + mic.

**Do a 20-second throwaway take first** to check your mic isn't clipping. Nearly
every bad hackathon video is bad because of audio, not video.

---

## The cut

Total: **4:40**. Under the 5:00 limit with room to breathe.

| time | screen | you say |
|---|---|---|
| 0:00–0:25 | your face | the problem |
| 0:25–1:10 | dashboard, top | the honest number |
| 1:10–2:10 | dashboard, walkthrough | one decision, end to end |
| 2:10–2:45 | Terminal B: `redteam.py` | the model is caged |
| 2:45–3:20 | Terminal B: `stress.py` | it survives being wrong |
| 3:20–3:50 | Terminal B: `evaluate.py` | the diagnosis is scored |
| 3:50–4:20 | Terminal B: `demo_failure.py` | it survives the gateway dying |
| 4:20–4:40 | your face | the close |

---

## 0:00–0:25 — the problem

**Face on camera.** This is the only part that needs to be you, and it's the
part that decides whether they watch the rest.

> "A payment fails. Most recovery tools retry it. But a card that's expired
> will fail every single time you retry it — you're just burning the merchant's
> success rate and annoying a customer.
>
> Recoup asks a different question first: *why* did this fail? And is chasing
> it even worth the money?"

---

## 0:25–1:10 — the honest number

**Browser, top of page.** Let the flow diagram finish animating before you
speak — it takes about 1.6 seconds and it earns the pause.

> "Nine lakh eighty-seven thousand rupees at risk. We recovered about one point
> one lakh of it.
>
> But look at the middle bar. Two point six lakh recovered on its own —
> customers who would have paid us anyway. Most dashboards count that as a win.
> We don't.
>
> Twenty percent of this book was deliberately left untouched. Our number is
> the difference between the group we worked and the group we didn't. It's the
> smaller number, and it's the only one we can defend."

**Scroll slowly to the stat strip.** Don't rush — this is your strongest
sixty seconds.

---

## 1:10–2:10 — one decision, end to end

**Click the scenario labelled "money we chose not to chase."**

Pick this one deliberately. An agent that succeeds is ordinary. An agent that
declines to act, and shows its arithmetic for why, is the thing they'll
remember.

Press play. Let it run. Talk over it:

> "One payment, and every step it took.
>
> The gateway gave us one string. We work out what it actually means, and how
> sure we are.
>
> Now the guardrails. Thirteen checks, and they run *before* we work out
> whether it's worth money — so no amount, and no AI model, can score its way
> past a compliance rule. Watch the chain break where one fails.
>
> Then the maths. Chance it works, times what we'd get back, minus what it
> costs, minus the goodwill we spend annoying someone. That comes out below our
> floor. So we do nothing, on purpose, and log why."

**If the chain doesn't visibly break**, click the scenario labelled *"an action
a guardrail stopped"* instead — that one always breaks.

---

## 2:10–2:45 — the model is caged

**Terminal B:**

```
python redteam.py
```

> "There's a language model in here — it reads the declines rules can't parse,
> and it writes the customer message. It is never trusted.
>
> Nine things models actually get wrong. Inventing an amount. Threatening legal
> action. Claiming a failed card hits your credit score. Proposing a retry
> against a risk decline.
>
> Seventeen out of seventeen. Every attack refused. The argument for putting a
> model near customer money isn't that it behaves — it's that it doesn't
> have to."

---

## 2:45–3:20 — it survives being wrong

**Terminal B:**

```
python stress.py --n 800
```

Takes about 20 seconds. Let the table land, then point at it.

> "The fair objection to any simulation is that I wrote the world *and* the
> agent's beliefs about the world, so of course they agree.
>
> So this breaks them apart. Scrambles what actually works, then inverts it.
>
> The claimed lift collapses — twenty-five points down to eleven. That's
> correct. That's what an honest measurement does when the beliefs behind it
> are wrong.
>
> But look at the right column. Zero forbidden retries in every world. A wrong
> belief can make this agent useless. It can't make it non-compliant."

---

## 3:20–3:50 — the diagnosis is scored

**Terminal B:**

```
python evaluate.py --n 2000
```

> "The diagnosis is scored against ground truth the engine never sees. The
> generator corrupts fourteen percent of gateway codes on purpose.
>
> When the code is honest, ninety-nine percent. When it goes generic, we
> abstain and a human takes it. When the gateway sends a code that's wrong but
> plausible — zero. We can't recover that, and I'd rather show you the zero
> than a blended eighty-seven."

---

## 3:50–4:20 — it survives the gateway dying

**Terminal B:**

```
python demo_failure.py
```

> "And when Razorpay stops answering — four failures, the circuit opens, and we
> stop calling. Nothing is dropped. Everything queues with a reason. Then it
> probes, recovers, and drains the queue.
>
> Failing loudly and keeping the work is the whole job."

---

## 4:20–4:40 — the close

**Face on camera.**

> "Every number you've seen runs from a clean clone with one command. No
> dependencies, no API keys needed.
>
> The number is a demonstration. The method is the submission."

Stop recording. Don't add an outro card, don't add music.

---

## After

- Watch it once at 2× with the sound off. If the screen is unreadable at any
  point, re-record that segment.
- Watch it once with your eyes closed. If it doesn't make sense as audio alone,
  you're talking too little.
- Upload to YouTube as **Unlisted**.
- Open the link in a private window to confirm it plays.

---

## If you only have 20 minutes

Cut to **90 seconds**: 0:00–0:25 (problem), 0:25–1:10 (honest number),
1:10–1:30 (`redteam.py`), then the close.

The control group and the caged model are the two things nobody else will have.
Everything else is supporting evidence.
