# Haqdaar Voice - Build Steps

**For:** Claude Code / Antigravity
**Scope:** the outer system. Telephony is NOT in these steps.
**Assume:** the call is already connected and the engine is handed events.

---

## How to use this file

Each step below is a **self-contained prompt**. Paste one into the agent, let
it finish, check the acceptance test, then move on. Do not paste two at once.

Every step ends with something runnable. If a step fails its acceptance test,
fix it before continuing - the later steps assume the earlier ones work.

**Files the agent needs in the repo before starting:**
`question_bank.yaml`, `attribute_schema.sql`, `attribute_seed.sql`,
`scheme_catalogue.sql`, `validate_bank.py`, `SYSTEM_DESIGN.md`, `.env.example`

---

## Testing is not optional

**A step is not done until its tests pass.** Every step below ends with a
`TESTS` line naming specific cases from `TEST_CASES.md`. Run those before moving
on. Do not batch testing to the end - a narrowing bug found at Step 3 costs ten
minutes, and the same bug found at Step 8 costs the demo.

When you hand a step to Claude Code or Antigravity, paste the step **and** its
test IDs. Tell the agent the tests are the acceptance criteria.

If a test cannot pass yet because a later step is missing, write it as a skipped
test with a reason. Never delete it.

---

## Step 0 - Project skeleton (10 min)

> **TESTS:** A-none yet. Verify: repo runs, `python3 -c "import app"` succeeds, `.env` absent does not crash.

> Create a Python project called `haqdaar`. Use a plain `src/` layout, no
> framework scaffolding. Add `pyproject.toml` with dependencies: `pyyaml`,
> `fastapi`, `uvicorn`, `httpx`, `pytest`, `python-dotenv`.
>
> Create these empty modules with docstrings only:
> `src/haqdaar/db.py`, `engine.py`, `bank.py`, `narrow.py`, `select.py`,
> `menu.py`, `api.py`, `sim.py`, `voice.py`.
>
> Copy `.env.example` to `.env` and load it with `python-dotenv` in a
> `config.py`. Every config value must have a working default so the app runs
> with an empty `.env`.
>
> Add `.env`, `*.db`, `__pycache__` to `.gitignore`. Initialise git, commit.

**Acceptance:** `python -c "import haqdaar.config"` works with no `.env` present.

---

## Step 1 - Database and ingest (25 min)

> **TESTS:** A1 A2 A3 A4 A5 A6 A7 A8 A9. **A4, A5, A8 are BLOCKERS.**

> Build `src/haqdaar/db.py`.
>
> Create the SQLite database by running, in order: `attribute_schema.sql`,
> `attribute_seed.sql`, `scheme_catalogue.sql`. Expose `get_db()` returning a
> connection with `foreign_keys = ON` and `row_factory = sqlite3.Row`.
>
> Write `scripts/ingest.py` that reads a JSON file of schemes with this exact
> shape (see the worked example in `scheme_catalogue.sql`):
> `scheme_name, slug, details, benefits, eligibility, application, documents,
> level, schemeCategory[], tags[]`
>
> For each scheme:
> 1. Insert the raw row verbatim. Never edit source prose.
> 2. Derive `state_scope` by scanning `details` and `eligibility` for Indian
>    state and UT names. Store the 2-letter code. **If unsure, leave NULL.**
>    NULL means the scheme is never eliminated by state, which is the safe
>    failure direction.
> 3. Leave `name_short_hi` and `benefit_one_line` NULL for now - Step 8.
> 4. `verified = 0` always.
> 5. Populate `scheme_categories` and `scheme_tags`.
> 6. Rebuild `schemes_fts`.
>
> Make ingest **idempotent** - re-running it must not duplicate rows.
>
> Also write `scripts/seed_demo.py` that inserts 20 synthetic schemes with
> realistic `scheme_rules` so the rest of the system can be built and tested
> before the real data lands.

**Acceptance:** `python scripts/seed_demo.py && sqlite3 haqdaar.db "select count(*) from schemes"` returns 20. Running it twice still returns 20.

---

## Step 2 - Question bank loader (20 min)

> **TESTS:** L3 (malformed YAML refuses to start). Plus `validate_bank.py` exits OK.

> Build `src/haqdaar/bank.py`.
>
> Load `question_bank.yaml`. Validate it on load using the same rules as
> `validate_bank.py` and **raise on error** - a malformed bank must never
> reach a caller.
>
> Expose:
> - `load_bank(path) -> Bank`
> - `Bank.question(qid)`
> - `Bank.askable(answers) -> list[Question]` - every question whose `requires`
>   is satisfied by the current answers and which has not been asked yet
> - `Bank.controls` - the `0` / `*` / `#` map
> - `Bank.policies` - `silence_ladder`, `speech_policy`, `invalid_policy`
>
> The `requires` mini-language has exactly four forms and nothing else:
> `always` | `<attr> == 'value'` | `<attr> in ['a','b']` | `<attr> is answered`
>
> Attributes starting with `_` are session scratch, not scheme attributes.

**Acceptance:** `Bank.askable({})` returns only `Q001_LANGUAGE`. After answering language and purpose, it returns a larger set that excludes both.

---

## Step 3 - Narrowing engine (30 min) - THE IMPORTANT ONE

> **TESTS:** B1 B2 B3 B4 B5 B6 B7 B8 B9. **B1, B4, B6 are BLOCKERS.** B1 first, before anything else.

> Build `src/haqdaar/narrow.py`.
>
> `narrow(answers: dict) -> list[Candidate]` returns surviving schemes ranked
> by soft-rule score.
>
> **Three-valued matching. This is the core rule of the whole product:**
> - `satisfied` - the answer meets the rule. Keep.
> - `violated` - the answer contradicts the rule. Drop **only if `hard = 1`**.
>   If soft, keep but subtract `weight` from the score.
> - `unknown` - the attribute is unanswered. **Keep. Always.**
>
> **An unanswered attribute must never eliminate a scheme.** Implement
> elimination as `NOT EXISTS (violated hard rule)`, not as `EXISTS (satisfied
> rule)`. These are not equivalent and the second one is the bug.
>
> Support ops: `eq`, `in`, `not_in`, `gte`, `lte`. Ordered comparisons use the
> `ord` column on `attr_values`, not string comparison.
>
> Write tests first:
> - empty answers returns ALL schemes
> - one hard violation drops exactly that scheme
> - one soft violation keeps it with a lower score
> - an unanswered attribute eliminates nothing

**Acceptance:** all four tests pass. The empty-answers test is the one that catches the classic bug.

---

## Step 4 - Question selector (25 min)

> **TESTS:** J1 J2 J3 J4 J5 J6 J7. **J7 is a GATE.** Test J1 with the API key removed.

> Build `src/haqdaar/select.py`.
>
> `pick_question(answers, candidates, bank) -> Question`
>
> 1. Get askable questions from the bank.
> 2. Score each by **information gain**: for every option, compute how many
>    candidates would survive, then prefer the question whose worst-case
>    surviving set is smallest. Break ties with the question's `gain` field.
> 3. Take the top 5.
> 4. Send those 5 to the LLM with the current answers and each question's `why`
>    field. Ask for one question ID.
> 5. **Fallbacks - all three take rank 1:** no API key, response after 700ms,
>    or an ID not in the shortlist.
>
> The LLM receives only question IDs and `why` text. It never sees SQL, never
> sees scheme names, and never returns anything but an ID from the list. Log
> every fallback with its reason.

**Acceptance:** with `LLM_API_KEY` unset, selection still works and returns rank 1 every time. Test suite runs with no network.

---

## Step 5 - The state machine (40 min) - THE HEART

> **TESTS:** C1-C8, D1-D8, E1-E7, F1-F6, G1-G6, I1-I6. **C5, D7, E7, I6 are BLOCKERS.**

> Build `src/haqdaar/engine.py`.
>
> ```
> step(state, event) -> (new_state, actions)
>
> event   = {"dtmf": "1"} | {"speech": "...", "confidence": 0.8}
>         | {"timeout": 5} | {"hangup": True}
> actions = [{"say": "..."}, {"gather": {...}}, {"end": True}]
> ```
>
> **The function must be pure.** Same state plus same event always produces the
> same result. No clock reads, no DB writes, no network inside `step`. This is
> what makes the whole system testable without a phone and replayable from a log.
>
> `state` holds: `phase`, `language`, `answers`, `asked[]`, `current_question`,
> `last_spoken`, `candidates`, `invalid_count`, `speech_attempts`,
> `silence_elapsed`, `menu_path`.
>
> Handle, in this priority order:
> 1. **Global keys first, at every node.** `0` main menu and wipe answers.
>    `*` replay `last_spoken`. `#` undo the last answer AND re-run narrowing.
> 2. **`#` must re-run the matcher.** Undoing the answer while leaving
>    `candidates` narrowed is an invisible bug - the call sounds correct and
>    the result is wrong. Test this explicitly.
> 3. Normal answer keys `1`-`9`.
> 4. Unclear speech: 2 attempts, saying "Mujhe samajh nahi aaya. Dobara
>    boliye.", then force DTMF.
> 5. Wrong button: 3 attempts, replaying options only (not the whole question),
>    then collapse to a 2-option menu.
> 6. Silence ladder: 5s nudge, 15s two options, 25s "Kya aap line par hain?",
>    30s end politely.
>
> Stop narrowing when candidates are 5 or fewer, or 10 questions asked. **There
> is no elapsed-time cutoff.**
>
> Zero candidates: undo the last answer and pick a different question. Never
> tell a caller there is nothing for them.
>
> `Q001_LANGUAGE` is DTMF-only: no speech, no barge-in. On invalid, replay. On
> timeout, default to Hindi rather than ending.

**Acceptance:** unit tests drive full calls as event lists with no I/O at all. Include a test where `#` is pressed mid-call and asserts `candidates` grew back.

---

## Step 6 - Number tree navigator (20 min)

> **TESTS:** H1 H2 H3 H4 H5 H6 H7 H8 H9 H10 H11. **H8 is a GATE.**

> Build `src/haqdaar/menu.py`.
>
> Three levels, from `scheme_catalogue.sql`:
> problem statement (`1`-`6`), scheme slot (`1`-`5`), section (`1`-`5`).
>
> - `main_menu()` - the 6 problem statements
> - `schemes_for(ps_key)` - up to 5 schemes
> - `section_text(slug, sec_key)` - **verbatim** column text from `schemes`
> - `resolve_code("231")` - direct dial, jump straight to that section
>
> Rules:
> - Never speak more than 6 options at a level.
> - Speak digits as digits: "do teen ek", never "two hundred thirty one".
> - Never list `0` `*` `#` as options. They are global and taught once.
> - Section text is returned **exactly as stored**. No summarising, no
>   rephrasing, no model in this path at all.
> - If `verified = 0`, strip any deadline sentence before speaking.

**Acceptance:** `resolve_code("611")` returns the fisherman scheme's benefits text, byte-identical to the DB column.

---

## Step 7 - HTTP API (20 min)

> **TESTS:** L5 L6 L7 L8. Two concurrent calls must not mix sessions.

> Build `src/haqdaar/api.py` with FastAPI.
>
> - `POST /call/start` returns `{call_id, actions}`
> - `POST /call/event` takes `{call_id, event}`, returns `{actions}`
> - `GET /call/{id}/state` returns the state, for debugging
> - `GET /health` reports whether the DB loaded and the bank validated
>
> Sessions live in an in-memory dict keyed by `call_id`. **Caller answers are
> never written to disk** - only anonymous counters may be logged.
>
> This layer does nothing but translate HTTP to `step()` and back. All logic
> stays in the engine. When the Twilio adapter arrives later, it becomes a
> second client of these same endpoints and nothing in the core changes.

**Acceptance:** `curl -X POST localhost:8000/call/start` returns the language prompt.

---

## Step 8 - Simulator (20 min) - THE DEMO

> **TESTS:** M1-M14 as scripted key sequences. **L1 is a BLOCKER** - full call with no API keys.

> Build `src/haqdaar/sim.py`, a terminal client of the HTTP API.
>
> Print each `say` action. Read a keypress for each `gather`. Accept `0`, `*`,
> `#`, digits, and a `!silence` command to simulate a timeout.
>
> Add flags:
> - `--script 1,2,3,#,1` to replay a fixed key sequence
> - `--trace` to show candidate count and the chosen question after each turn
> - `--seed N` for reproducible runs
>
> The trace view matters more than it sounds. Watching the candidate count drop
> from 100 to 4 over six questions is how you tell the narrowing is working,
> and it is the most convincing thing to show a judge.

**Acceptance:** a full call completes end to end in the terminal with `.env` renamed away.

---

## Step 9 - Content pass (parallel, human work)

> **TESTS:** K1 K2 K3 K4 K5 K6 K7. **K1 is THE ship gate** - 10 calls, zero invented names.

Not an agent step. This is the slow one and it decides whether answers are correct.

For each scheme:
1. `name_short_hi` - 8 words maximum. The raw names are 20 words and unspeakable.
2. `benefit_one_line` - 25 words maximum.
3. Assign `(ps_key, slot_key)`. **Never reshuffle once assigned.**
4. Convert eligibility prose to `scheme_rules`. Typically 3-6 rows. `hard = 1`
   only where the text states an absolute bar. When in doubt, soft.
5. Set `verified = 1` only after a human checks it against the source.

See the fully worked fisherman example in `scheme_catalogue.sql`.

**Twenty good schemes beat a hundred rough ones.** Start with the 20 most
commonly asked-about.

---

## Step 10 - Sarvam audio (25 min)

> **TESTS:** E7 L4 again against the real API. Outage must degrade to buttons.

> Build `src/haqdaar/voice.py`.
>
> - `tts(text, lang) -> audio bytes`, cached to `cache/tts/<sha1>.wav`
> - `stt(audio, lang) -> (text, confidence)`
>
> **Cache aggressively.** Every prompt is static, so after one warm-up run the
> system makes almost no TTS calls. This also removes rate limits as a demo risk.
>
> With no `SARVAM_API_KEY`, `tts` prints the text and `stt` returns low
> confidence, which the engine already handles as "unclear" and routes to
> buttons.
>
> Add `scripts/warm_cache.py` to pre-generate all 81 prompts.

**Acceptance:** with the key absent, a full simulated call still completes.

---

## Step 11 - Telephony adapter (LATER, not tonight)

> **TESTS:** Deferred with the step. See `future/02_TELEPHONY_CHECKS.md`.

> Build `src/haqdaar/twilio_adapter.py`.
>
> Translate Twilio webhooks into engine events and engine actions into TwiML.
> **Add nothing to the engine.** If the adapter needs new engine behaviour,
> the engine is wrong.
>
> Verify demo handsets on the trial account first - trial accounts can only
> call verified numbers, and that is a 5 AM surprise you do not want.

---

## Order and parallelism

```
Step 0 -> 1 -> 2 -> 3 -> 5 -> 8      the critical path
              4, 6, 7  can follow 3 in any order
              9        runs in parallel the whole time, by hand
              10, 11   last, both optional for a working demo
```

**A demo exists from Step 8 onward.** Everything after improves it; nothing
after is required for it to run.

---

## Gates

| After | Must be true |
|---|---|
| Step 3 | Empty answers returns every scheme |
| Step 5 | A full call runs as a list of events, no I/O |
| Step 6 | Section text is byte-identical to the DB |
| Step 8 | A call completes with no API keys at all |
| Step 9 | 10 test calls, **zero invented scheme names** |

The last one is the ship gate. Everything else is negotiable.
