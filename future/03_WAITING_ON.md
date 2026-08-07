# Waiting on someone else

Keep this current. At 4 AM this is the only file worth reading.

| # | What | Who | Blocks | Severity | Status |
|---|---|---|---|---|---|
| 1 | The 100 schemes as JSON | Jarvis | **The matcher. Everything.** | CRITICAL | waiting |
| 2 | Scheme number list (scheme -> dial number) | Jarvis | Dial-a-scheme branch only | medium | waiting |
| 3 | Keymap file | Team | Nothing - keys locked | low | waiting |
| 4 | Figma flowchart to GUIDE.md | Team | Nothing - default flow works | low | in progress |
| 5 | Sarvam API key | Team | Voice only, not buttons | medium | waiting |

---

## Only item 1 is real

Everything else has a working default already committed.

**Minimum viable version of item 1:** 20 schemes with 3-6 eligibility rules each.
Not 100. Twenty is enough to show the count dropping convincingly. The other 80
can sit in the database with a name and no rules - they never get eliminated,
which is harmless.

**Format:** exactly the JSON shape you already sent. Do not clean it up.
`scheme_catalogue.sql` has the full worked example.

---

## Known gaps in the scheme data itself

Found while mapping your sample. These apply to all 100.

| Gap | Why it matters | What we do |
|---|---|---|
| **No state field.** "Puducherry" only appears inside `details` prose | `state` is our biggest narrowing lever | Extract at ingest. If unsure, leave NULL = never eliminated |
| **Names are unspeakable.** Sample is 20 words with nested quotes | Nobody can listen to that | Every scheme needs a hand-written short Hindi name |
| **`benefits` is a wall of text** | Cannot be read aloud | Every scheme needs a 25-word one-liner |
| **Not every eligibility line is a rule.** "lost his life while fishing" | We will never ask that on a phone | Claim conditions stay as spoken text, not rules |
| **Deadlines sit inside `application` prose** | Honesty rule: only speak deadlines from verified rows | Leave `verified = 0` unless a human checked it |

---

## Decisions still open

| Question | Why it matters | Default if unanswered |
|---|---|---|
| Zero schemes match mid-call | Rare but embarrassing on stage | Undo last answer, ask something else |
| Voice on for the demo, or buttons only? | Whole class of live failure | Voice on **only** for the scheme-name node |
| Does `0` wipe answers or keep them? | Engine state handling | Wipes - it is a fresh start |
| No key reaches a human any more | Fine for demo, real gap in production | Parked |
