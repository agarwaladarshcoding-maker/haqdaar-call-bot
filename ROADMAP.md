# What's left, in one line each

Status as of Steps 0-8 complete. The system is a fully working, fully
offline call simulation end to end — Step 9 (content) is manual work that
can run in parallel, and Steps 10-11 are the only ones that need any
external API key.

| Step | What it builds | Needs any API key / external service? |
|---|---|---|
| 5 — `engine.py` (**the heart**) — done | The state machine that turns "caller pressed 3" into "here's what to say and listen for next," including undo (`#`), restart (`0`), repeat (`*`), and the fallback ladders for silence/wrong-buttons/unclear-speech | No — pure Python, works entirely offline |
| 6 — `menu.py` — done | Lets a caller who already knows a scheme's number dial it directly, or browse by category instead of answering narrowing questions | No |
| 7 — `api.py` — done | Wraps the engine in plain HTTP endpoints (start a call, send an event, check state, health check) so anything can drive a call the same way. Sessions are in-memory only, never written to disk | No — just a web server on your own machine |
| 8 — `sim.py` (**the demo**) — done | A terminal program you can type keypresses into and watch a full fake call happen start to finish, talking to `api.py` over real HTTP. `--trace` shows the candidate count shrinking turn by turn; `--dial CODE` jumps straight to a scheme's text the way a caller with a written-down code would | No |
| 9 — content pass — **next, and the only step left before this is real** | *Not code* — hand-writing the real eligibility rules for all 100 real schemes (right now only the 20 fake demo schemes have rules) | No, this is manual writing work, done in parallel by whoever's editing the scheme data |
| 10 — `voice.py` (Sarvam) | Turns the system's Hindi/English text into spoken audio, and turns a caller's spoken answer into text | **Yes — Sarvam API key.** Without it, the system just prints text instead of speaking, and treats all speech as "didn't understand" — falls back to keypad automatically, doesn't break |
| 11 — Twilio adapter | Connects the system to an actual phone number so real people can call it | **Yes — Twilio account + phone number.** This is the only step that makes it a real phone call instead of a simulation |

## Where the optional pieces plug in (and why they're optional)

**LLM (already wired up, in Step 4, today):** used only as an optional tie-breaker when picking the next question — "if there are 5 equally good next questions, ask an AI which one sounds most natural here." If `LLM_API_KEY` is never set, or the AI is slow/wrong/broken, the system silently uses its own deterministic pick instead. The system's actual decisions (which schemes qualify, which question to ask) never depend on the LLM being present — it can only make phrasing marginally better, never make the system smarter or dumber about eligibility.

**Sarvam (Step 10, not started):** the only thing that turns text into speech and speech into text. Until this exists, the entire system is text-only — which is exactly what `sim.py` (Step 8, done) demos today. Nothing before Step 10 needs it.

**Twilio (Step 11, later, explicitly "not tonight" in the original plan):** the only thing that connects this to a real telephone number. Everything through Step 8 is already a complete, fully working system you can operate from a terminal — Twilio just gives it a phone number to answer.

**In short:** Steps 0-8 are done — a complete, correct, fully offline call simulation, runnable right now with `python -m haqdaar.sim --trace` and zero API keys. Step 9 is content, not code, and is the only thing standing between this and real scheme data. Steps 10-11 are what turn the simulation into an actual phone call a real person can dial — nothing about the *logic* changes when that happens, only how the words get in and out.
