# What's left, in one line each

Status as of Step 4 (question selector) complete, awaiting your review before push.

| Step | What it builds | Needs any API key / external service? |
|---|---|---|
| 5 — `engine.py` (**the heart**) | The state machine that turns "caller pressed 3" into "here's what to say and listen for next," including undo (`#`), restart (`0`), repeat (`*`) | No — pure Python, works entirely offline |
| 6 — `menu.py` | Lets a caller who already knows a scheme's number dial it directly, or browse by category instead of answering narrowing questions | No |
| 7 — `api.py` | Wraps the engine in plain HTTP endpoints (start a call, send an event, check state) so anything can drive a call the same way | No — just a web server on your own machine |
| 8 — `sim.py` (**the demo**) | A terminal program you can type keypresses into and watch a full fake call happen start to finish, using everything built so far | No |
| 9 — content pass | *Not code* — hand-writing the real eligibility rules for all 100 real schemes (right now only the 20 fake demo schemes have rules) | No, this is manual writing work, done in parallel by whoever's editing the scheme data |
| 10 — `voice.py` (Sarvam) | Turns the system's Hindi/English text into spoken audio, and turns a caller's spoken answer into text | **Yes — Sarvam API key.** Without it, the system just prints text instead of speaking, and treats all speech as "didn't understand" — falls back to keypad automatically, doesn't break |
| 11 — Twilio adapter | Connects the system to an actual phone number so real people can call it | **Yes — Twilio account + phone number.** This is the only step that makes it a real phone call instead of a simulation |

## Where the optional pieces plug in (and why they're optional)

**LLM (already wired up, in Step 4, today):** used only as an optional tie-breaker when picking the next question — "if there are 5 equally good next questions, ask an AI which one sounds most natural here." If `LLM_API_KEY` is never set, or the AI is slow/wrong/broken, the system silently uses its own deterministic pick instead. The system's actual decisions (which schemes qualify, which question to ask) never depend on the LLM being present — it can only make phrasing marginally better, never make the system smarter or dumber about eligibility.

**Sarvam (Step 10, not started):** the only thing that turns text into speech and speech into text. Until this exists, the entire system is text-only — which is exactly what `sim.py` (Step 8) will demo. Nothing before Step 10 needs it.

**Twilio (Step 11, later, explicitly "not tonight" in the original plan):** the only thing that connects this to a real telephone number. Everything through Step 10 is a complete, fully working system you can operate from a terminal — Twilio just gives it a phone number to answer.

**In short:** Steps 5-8 (what we're doing now) get you a complete, correct, fully offline call simulation. Step 9 is content, not code. Steps 10-11 are what turn that simulation into an actual phone call a real person can dial — nothing about the *logic* changes when that happens, only how the words get in and out.
