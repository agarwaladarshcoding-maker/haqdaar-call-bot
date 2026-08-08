# Haqdaar — read this first

**Haqdaar is a phone helpline that finds Indian government schemes for you.**
You call a number, you talk in Hindi, and in under six questions it tells you
which schemes you can actually get. No app, no internet, no reading. A basic
feature phone is enough.

---

## Please read this before you try to call

**The number will not answer unless our backend is switched on.**

We want to be upfront about this rather than have you dial, hear nothing, and
assume the project is broken.

Haqdaar is not a website sitting on a server somewhere. It is a **backend
service** — the brain of the call — and right now that brain runs on our
laptop. The phone number is connected to it through a temporary tunnel that
only exists while we have the system running.

So there are two states:

| Our backend | What happens if you call |
| --- | --- |
| **Running** | The call works completely. Real voice, real questions, real schemes. |
| **Switched off** | The number rings into nothing. Not a bug — there is simply nobody home. |

This is a deliberate choice for the hackathon, not a missing piece. Hosting it
permanently is a deployment step, not an engineering one — the same code runs
unchanged the moment it is put on a server. What we chose to spend our time on
was making the call itself actually work.

### So how do you see it live?

**Just ask us, and we will switch it on in front of you.** It takes about
thirty seconds — one command starts the backend, and the phone number is live.

Even better: **we can make it call you.** You do not have to dial anything, and
you do not need international roaming — an incoming call costs you nothing.
Give us your number and your phone rings.

```bash
./run.sh                                       # backend on
python -m scripts.call_me --to +91XXXXXXXXXX   # your phone rings
```

While you are on the call, **watch our terminal**. Every question, everything
you say, and the number of schemes still in the running scroll past live. That
is the part worth seeing — you can watch 100 schemes become 4 while you talk.

---

## What a real call sounds like

This is an actual call, not a script we wrote for the slide.

```
Haqdaar : Namaste. Kya aapko kisi yojna ka naam pata hai?
          Haan to 1 dabaiye. Nahi, madad chahiye to 2 dabaiye.        100 schemes

You     : [presses 2]

Haqdaar : Bataiye, aapko kis cheez ke liye madad chahiye?

You     : "main kisan hoon aur mujhe kheti ke liye madad chahiye"
          → understood as: persona = farmer, theme = farming            → 6 schemes

Haqdaar : Mukhya fasal kya hai? Dhaan ke liye 1 dabaiye.
          Nariyal ke liye 2 dabaiye. Ganna ke liye 3 dabaiye...

You     : [presses 1 — dhaan]                                           → 4 schemes

Haqdaar : Yeh yojnaayein mil sakti hain: Back Ended Investment Subsidy
          to Paddy Growers — Paddy growers ko Rs. 4,000/- per hectare
          back-ended investment subsidy milegi.
```

**One sentence took 100 schemes down to 6.** One keypress took it to 4.

The important thing to notice: **nobody wrote that crop question into a
script.** The system ranked all 47 questions it knows and picked the one that
would eliminate the most schemes, *given that you had just said you were a
farmer*. Say something else and it asks something else. That is the whole idea.

---

## How it works, in plain words

Think of it as three parts.

**1. The ears and the mouth.** Sarvam turns your speech into text, and text
back into a voice. We do not use Google or Twilio's own speech recognition,
because real people say things like *"mujhe kheti ke liye help chahiye"* — half
Hindi, half English — and those systems expect one language at a time.

**2. The brain that decides what to ask.** We have 100 schemes and 372
eligibility rules. After every answer, the system re-checks which schemes are
still possible, then picks the single question that would narrow the list
fastest. It stops at six questions, because nobody stays on a helpline longer
than that.

**3. The language model — kept on a short leash.** An AI reads your sentence
and turns it into structured facts (*"farmer"*, *"farming"*). But it is
**never** allowed to decide who qualifies. That is done by fixed rules in a
database. Everything the AI produces is checked against a list of things we
already understand, and thrown away if it does not match.

That last point matters more than it sounds. This is a system that tells people
what government money they can claim. **If the AI invented a scheme, or invented
a rupee figure, a real person would act on it.** So:

- It can only ever pick from schemes we actually hold — never name a new one.
- If your scheme is not in our catalogue, it says so plainly instead of guessing.
- Any amount read out loud must be text we already had, word for word.
- If it hears a scheme name, it always **reads it back to you** before acting.

**And if the AI is down, the call still works.** It falls back to plain
number-key menus. You lose the ability to speak freely; you do not lose the
call.

---

## Why the number needs our backend — the honest version

A phone call is not like opening a website. When you dial, the phone network
hands the call to Twilio, and Twilio has to ask *someone* what to say next —
after every single thing you press or say. That "someone" is our backend.

```
your phone → phone network → Twilio → ??? → our backend
                                       ↑
                            this arrow only exists
                            while our system is running
```

Right now that arrow is a temporary tunnel from the internet to our laptop.
Close the laptop and the arrow disappears. Put the same code on a server and
the arrow is permanent — nothing else changes.

We would rather show you a system that genuinely works when switched on, than
one that is always reachable and falls over when you actually use it.

---

## What is real, and what is not — no overclaiming

Things we would rather tell you than have you find out:

- **100 schemes**, ingested from a real government catalogue with 372 machine-readable eligibility rules. Not a demo dataset of five.
- **97 of 100** benefit lines are translated into Hindi. The other three failed our own check — we verify that every rupee figure survives translation, and if it does not, we keep the English rather than speak a wrong number.
- **Twilio trial account.** There is a short "you have a trial account" message before our call begins. That is Twilio's, not ours.
- **The catalogue is a snapshot**, not a live government feed. Adding new schemes is a data job, not a code change.
- **633 automated tests.** Most of them exist to prove the system degrades safely — no API key, a timeout, a bad response — rather than to prove it works on a good day.

---

## Run it yourself

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env          # add your Sarvam / Groq / Twilio keys
./run.sh                      # backend up, terminal shows every call live
```

No phone, no keys, no internet? The same engine runs in your terminal:

```bash
PYTHONPATH=src .venv/bin/python -m haqdaar.sim
```

Same code, same questions, same schemes — just typed instead of spoken. We
keep it open in a second tab in case the venue wifi dies mid-demo.

**Architecture diagram:** [`docs/architecture.mmd`](docs/architecture.mmd) — it
shows what happens before a call (slow work, done once) versus during a call,
where every turn has to answer within 15 seconds or the phone network hangs up.
That one constraint explains almost every design decision we made.
