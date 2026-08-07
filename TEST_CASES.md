# Haqdaar Voice - Test Cases

**Rule: no step is done until its tests pass.**

Each test is `given / when / then` so it can be pasted straight into Claude Code
or Antigravity as a spec. **BLOCKER** must pass before we demo. **GATE** are the
ship criteria. Run order matches `BUILD_STEPS.md`.

---

## A. Data and ingest (Step 1)

| # | Given | When | Then |
|---|---|---|---|
| A1 | The 100-scheme JSON | Ingest runs | Exactly 100 rows in `schemes` |
| A2 | Ingest already ran | Ingest runs again | Still 100 rows, no duplicates |
| A3 | Prose naming one state | Ingest runs | `state_scope` is that 2-letter code |
| A4 | Prose naming no state | Ingest runs | `state_scope IS NULL`, not a guess |
| A5 | Prose naming two states | Ingest runs | `state_scope IS NULL` |
| A6 | Any scheme | Ingest runs | `benefits` byte-identical to the JSON |
| A7 | Any scheme | Ingest runs | `verified = 0` |
| A8 | Ingest run twice | Compare | Every `scheme_no` unchanged |
| A9 | All schemes | Ingest runs | Every scheme reachable from some menu path |

**A4 and A5 are BLOCKERS.** A wrong state silently hides correct schemes and
nothing in the call surfaces the mistake.

**A8 is a BLOCKER.** Dial codes get spoken aloud and written on paper. If a
re-run renumbers schemes, every code a caller noted now points somewhere else.

---

## B. Narrowing engine (Step 3) - highest-risk area

| # | Given | When | Then |
|---|---|---|---|
| B1 | No answers | `narrow({})` | Returns all 100 schemes |
| B2 | Answer violates a `hard` rule | `narrow(...)` | That scheme is gone |
| B3 | Answer violates a `soft` rule | `narrow(...)` | Kept, score reduced |
| B4 | Attribute never asked | `narrow(...)` | Nothing eliminated because of it |
| B5 | Caller in WB, scheme scoped PY | `narrow(...)` | PY scheme dropped |
| B6 | Caller in WB, scheme state NULL | `narrow(...)` | Scheme kept |
| B7 | Answers satisfying nothing | `narrow(...)` | Returns 0, engine undoes and recovers |
| B8 | Ordered attr with `gte` | `narrow(...)` | Compares by `ord`, not alphabetically |
| B9 | Same answers twice | `narrow(...)` | Identical result, no hidden state |

**B1 is the single most important test in the project.** If it fails, the engine
is using `EXISTS (satisfied)` instead of `NOT EXISTS (violated hard)`, and it
will hide schemes from every caller who has not answered enough questions yet.

**B4 and B6 are BLOCKERS** for the same reason.

---

## C. Global keys (Step 5)

| # | Given | When | Then |
|---|---|---|---|
| C1 | Any node | Press `0` | Back to language, answers wiped |
| C2 | Any node | Press `*` | Replays `last_spoken` exactly |
| C3 | Just heard an answer | Press `*` | Replays the answer, not the question |
| C4 | Three questions in | Press `#` | Last answer removed, previous question re-asked |
| C5 | Three questions in | Press `#` | Candidate count grows back |
| C6 | At the first question | Press `#` | Stays put, does not crash or exit |
| C7 | `#` pressed 5 times | - | Lands at the first question, no negative index |
| C8 | `0` mid-narrowing | - | `asked[]` empty, candidates back to 100 |

**C5 is a BLOCKER and the nastiest bug in this system.** If `#` removes the
answer but leaves `candidates` narrowed, the call sounds perfect and the result
is silently wrong. Assert the count, not just the prompt text.

---

## D. Language node (Step 5)

| # | Given | When | Then |
|---|---|---|---|
| D1 | Call connects | - | Offers 1 Hindi, 2 English, 3 both |
| D2 | Language node | Speak instead of press | Ignored, no STT at this node |
| D3 | Language node | Talk over the prompt | No barge-in, prompt finishes |
| D4 | Language node | Press `7` | Invalid, options replayed |
| D5 | Language node | Press `0` | Replays language prompt |
| D6 | Language node | Press `#` | Replays, does not exit the call |
| D7 | Language node | Silence 30s | Defaults to Hindi, call continues |
| D8 | 3 wrong presses | - | Collapses to 2 options, does not hang up |

**D7 is a BLOCKER.** Ending a call because someone hesitated at the very first
prompt is the worst failure available - they never heard a single scheme.

---

## E. Unclear speech (Step 5)

| # | Given | When | Then |
|---|---|---|---|
| E1 | Confidence below 0.6 | - | "Mujhe samajh nahi aaya. Dobara boliye." |
| E2 | Second unclear attempt | - | Same message, counter now 2 |
| E3 | Third unclear attempt | - | "Koi baat nahi. Button se kariye." then DTMF only |
| E4 | After DTMF fallback | Speak again | Ignored, buttons still required |
| E5 | Unclear then valid keypress | - | Accepted, speech counter resets |
| E6 | STT returns empty string | - | Treated as unclear, not a valid answer |
| E7 | STT throws or times out | - | Treated as unclear, call continues |

**E7 is a BLOCKER.** A Sarvam outage must degrade to buttons, never crash a live
call.

---

## F. Silence ladder (Step 5)

| # | Given | When | Then |
|---|---|---|---|
| F1 | 5s silence | - | Short nudge |
| F2 | 15s silence | - | Two options only |
| F3 | 25s silence | - | "Kya aap line par hain?" |
| F4 | 30s silence | - | Polite close, never a silent hangup |
| F5 | Input at 20s | - | Ladder resets fully |
| F6 | Silence while presenting | - | Ladder applies here too |

---

## G. Wrong buttons (Step 5)

| # | Given | When | Then |
|---|---|---|---|
| G1 | 4 options shown | Press `8` | "Yeh button sahi nahi hai", options replayed |
| G2 | Second wrong press | - | Options replayed, not the whole question |
| G3 | Third wrong press | - | Collapses to a 2-option menu |
| G4 | Wrong then right | - | Accepted, invalid counter resets |
| G5 | Rapid double-press `11` | - | One answer, second debounced |
| G6 | Press `9` where 9 is unused | - | Invalid, not silently mapped to option 1 |

---

## H. Menus and dial codes (Step 6)

| # | Given | When | Then |
|---|---|---|---|
| H1 | Any menu level | - | Never more than 6 spoken options |
| H2 | Any menu | - | `0` `*` `#` never listed as options |
| H3 | Level 1 option `1` | - | Offers the 6 business need groups |
| H4 | Level 1 options `2`-`6` | - | Goes straight to a scheme list |
| H5 | Bucket with 15 schemes | - | Paging works, nothing unreachable |
| H6 | Valid dial code | `resolve_code` | Lands on the right scheme section |
| H7 | Invalid dial code | - | Graceful message, back to main menu |
| H8 | Section requested | - | Text byte-identical to the DB column |
| H9 | Scheme with `verified = 0` | Section spoken | Deadline sentence stripped |
| H10 | Any spoken code | - | Read as digits: "shunya do ek" |
| H11 | Menu order changed | Re-run ingest | Dial codes still resolve identically |

**H8 is a GATE.** Any rewriting of scheme text is a hallucination path.

**H11 matters** because menus are browsing and dial codes are addressing. They
are deliberately decoupled - reshuffling a menu must never move a code.

---

## I. Stop conditions (Step 5)

| # | Given | When | Then |
|---|---|---|---|
| I1 | Candidates drop to 5 | - | Stops asking, presents |
| I2 | 10 questions asked, 40 candidates | - | Stops anyway, presents top 5 |
| I3 | Call runs long | - | No time-based cutoff mid-answer |
| I4 | Candidates hit 0 | - | Undo last answer, ask something else |
| I5 | Candidates hit 1 early | - | Presents immediately |
| I6 | No askable questions left | - | Presents what it has, does not loop |

**I6 is a BLOCKER** - the classic infinite-loop bug when `requires` gates end up
excluding every remaining question.

---

## J. LLM selector (Step 4)

| # | Given | When | Then |
|---|---|---|---|
| J1 | No API key | Selection | Rank 1 used, call proceeds |
| J2 | LLM slower than 700ms | - | Rank 1 used |
| J3 | Returns an ID not in the shortlist | - | Rank 1 used, fallback logged |
| J4 | Returns malformed JSON | - | Rank 1 used |
| J5 | Returns an already-asked ID | - | Rejected, rank 1 used |
| J6 | Errors 3 times | - | Disabled for the rest of the call |
| J7 | Any run | - | LLM output never reaches the caller as text |

**J7 is a GATE.** The model picks an ID. It never speaks.

---

## K. Content safety (Step 9)

| # | Given | When | Then |
|---|---|---|---|
| K1 | 10 full calls | Transcript reviewed | Zero scheme names not in the DB |
| K2 | Any benefit spoken | - | Matches the DB string exactly |
| K3 | Any amount spoken | - | Appears verbatim in the source |
| K4 | `verified = 0` scheme | - | No deadline stated as fact |
| K5 | Confirmation loop | - | Name and benefit both from the DB row |
| K6 | No scheme matches | - | Says so honestly, invents nothing |
| K7 | Scheme with `needs_human_name = 1` | Spoken | Reviewed name, not a truncated stub |

**K1 is THE ship gate.** Telling someone they qualify for money that does not
exist is worse than telling them nothing at all.

---

## L. Resilience (Steps 7 and 10)

| # | Given | When | Then |
|---|---|---|---|
| L1 | `.env` renamed away | Full call | Completes end to end |
| L2 | DB file missing | Startup | Clear error, does not start half-broken |
| L3 | Malformed `question_bank.yaml` | Startup | Refuses to start |
| L4 | Sarvam 429 rate limit | Mid-call | Falls back to cached or text |
| L5 | Two calls at once | - | Sessions never mix |
| L6 | Caller hangs up mid-question | - | Session cleaned up, no crash |
| L7 | Same `call_id` posted twice | - | Handled idempotently |
| L8 | Unknown `call_id` | - | 404, no crash |

**L1 and L3 are BLOCKERS.** L3 matters because a broken bank should fail on our
laptop at startup, not at question 4 on a live call.

---

## M. Full-call scenarios

Run each as a scripted key sequence through the simulator.

| # | Scenario | Expected |
|---|---|---|
| M1 | Happy path, all valid presses | 5-6 questions, schemes presented |
| M2 | `#` twice mid-call | Recovers, candidate count correct |
| M3 | `0` at question 4 | Full restart from language |
| M4 | Never speaks, only buttons | Completes normally |
| M5 | Speaks unclearly throughout | Degrades to buttons, still completes |
| M6 | Wrong button at every node | Reaches 2-option menus, still completes |
| M7 | Long silences at every node | Ends politely, no dead air |
| M8 | Knows the scheme name, says it | Confirmation loop, correct scheme |
| M9 | Names a scheme we do not have | Honest miss, offers the menu |
| M10 | Types a direct dial code | Jumps straight to that section |
| M11 | Contradictory answers | Zero candidates, recovers |
| M12 | `*` after every prompt | Replays correctly every time |
| M13 | Business caller, needs electricity help | Reaches `power_water`, 4 schemes |
| M14 | Farmer caller | Reaches `farming`, all 6 fit one page |

**M5, M6 and M7 are the demo-day tests.** A judge will absolutely mash the wrong
buttons and go quiet at the wrong moment.

---

## Priority if time runs short

Run these ten in order and stop worrying about the rest:

1. **B1** - no answers returns all 100
2. **C5** - `#` restores the candidate count
3. **L1** - runs with no API keys
4. **D7** - silence at language defaults to Hindi
5. **I6** - no askable questions does not loop forever
6. **H8** - section text byte-identical
7. **A8** - re-running ingest never renumbers
8. **K1** - zero invented scheme names
9. **M6** - wrong button everywhere still completes
10. **E7** - STT failure degrades to buttons

Those ten cover every way this system can fail in front of a judge.
