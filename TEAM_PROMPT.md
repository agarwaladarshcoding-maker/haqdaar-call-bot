# Handoff prompt - give this whole file to your teammates

Paste everything below the line into a fresh LLM session, and attach these four
files alongside it:

- `question_bank.yaml`
- `attribute_schema.sql`
- `attribute_seed.sql`
- `SYSTEM_DESIGN_v2.md`

They will not need to explain the project from scratch. Everything the model
needs is in the prompt plus those four files.

---

## PROMPT STARTS HERE

You are working on **Haqdaar Voice**, an IVR phone line that tells rural Indian
callers which government schemes apply to them and how to apply. No app, no
internet, no literacy required. Button-first, voice-optional, Hindi-first.
Hackathon build, team of four, demo in hours not weeks.

### What already exists and must not be redesigned

Four files are attached. Read all four before writing anything.

- **`question_bank.yaml`** - 80 questions, already written and already validated
  as a DAG. This is DATA, not code. It is the source of truth for what the line
  can ask.
- **`attribute_schema.sql`** - the attribute matrix. 60 attributes. Every
  question writes exactly one of them; every scheme rule references exactly one.
- **`attribute_seed.sql`** - the legal values for each attribute, with `ord` on
  banded ones so `gte` / `lte` comparisons work.
- **`SYSTEM_DESIGN_v2.md`** - the narrowing loop, the LLM contract, and the
  time budget. Section 3 is the whole system in one diagram.

### The architecture in five sentences

1. The engine keeps a set of answers and runs one fixed SQL statement after every
   turn to get the live candidate scheme set.
2. Matching is **three-valued**: a scheme is eliminated only when an answered
   attribute actively contradicts a `hard` rule. An unanswered attribute never
   eliminates anything.
3. The **DAG** (`requires:` in the question bank) decides which questions are
   askable right now, and therefore which buttons exist. Structure decides
   buttons; the model never invents an option.
4. The engine ranks askable questions by how much they would shrink the candidate
   set, takes the top five, and the **LLM picks one of those five**. Timeout or
   bad output means the engine takes rank 1 and the call continues.
5. The LLM never writes SQL, never names a scheme, and never picks a question
   outside the shortlist.

### Global keys - fixed, do not change

| Key | Action |
|---|---|
| `1`-`8` | Answer options only |
| `9` | Repeat the prompt |
| `0` | Transfer to a human |
| `*` | Go back one question |
| `#` | Start over |

Note: an earlier draft of the flowchart put Hindi on `0`. That is now fixed.
`0` is the human operator at every node without exception, because a caller who
presses `0` expecting a person must always get one. Language is on `1 / 2 / 3`.

---

## YOUR TASK

You are given a **flowchart drawn in Figma** describing the call flow. Convert it
into **`GUIDE.md`** - the single document the runtime agent is briefed with.

`GUIDE.md` must be reconciled against `question_bank.yaml`. Where the flowchart
and the bank disagree, **do not silently pick one**. Produce a conflict list.

### Deliverable 1 - `GUIDE.md`

Structure it exactly like this:

```markdown
# GUIDE.md - runtime brief

## 1. Identity and disclosure
What the line says in the first five seconds, including the AI disclosure.

## 2. Global keys
The five-row table above, verbatim.

## 3. Call phases
GREETING -> LANGUAGE -> INTENT -> NARROWING LOOP -> PRESENT -> DETAIL -> RECORD -> END
One short paragraph per phase: what the caller hears, what keys are live,
what ends the phase.

## 4. The narrowing loop
Restate SYSTEM_DESIGN_v2.md section 3 in plain language. State the three stop
conditions: 5 or fewer candidates, 10 questions asked, or 4:30 elapsed.

## 5. Question index
A table with one row per question in question_bank.yaml:
| id | asks (English) | writes | requires | keys |
Eighty rows. Generated from the YAML, not retyped.

## 6. What the agent must never do
- Never state a deadline unless the row has verified = 1
- Never name a scheme that did not come from the SQL result set
- Never answer pest, weather, or mandi price questions - transfer to a human
- Never answer "why has my money not come" - scripted honest answer, then transfer
- Never guess on low-confidence speech - fall back to the menu
- Never end a call with "you can cancel" - always a human handoff or a logged callback
- Never hold music, never a silent hangup

## 7. Out-of-scope scripts
Verbatim wording for each refusal above.

## 8. Conflict log
Every place the Figma flowchart and question_bank.yaml disagreed, and which
one was chosen. Empty is a valid answer only if you actually checked.
```

### Deliverable 2 - conflict list

A short list, separate from `GUIDE.md`, of anything in the Figma flowchart that:

- uses a reserved key (`0`, `9`, `*`, `#`) as an answer option
- offers more than eight options at one node
- asks a question whose `requires` cannot be satisfied at that point in the flow
- references an attribute not in `attribute_seed.sql`
- has a node with no exit for silence, no repeat, and no human

### Deliverable 3 - `scheme_rules` rows for the six demo schemes

For each of the six hand-verified schemes, write the `INSERT INTO scheme_rules`
statements. One row per eligibility condition. Every row needs a `source_quote`
copied verbatim from the scheme document - this is the audit trail and it is not
optional.

```sql
INSERT INTO scheme_rules (scheme_id, attribute, op, value, hard, weight, source_quote) VALUES
('PM_KISAN','owns_land','eq','yes',1,1.0,'All landholding farmer families...'),
('PM_KISAN','land_band','lte','gt5',0,0.5,'...'),
('PM_KISAN','occupation','in','["farmer"]',1,1.0,'...');
```

Rules for writing rules:

- `hard = 1` only when the scheme document states an actual disqualifier. If you
  are inferring, use `hard = 0` with a weight.
- Use only attributes present in `attribute_seed.sql`. If you need a new one,
  add it to the conflict list instead of inventing it.
- `in` and `not_in` take a JSON array as a string. Everything else takes a scalar.
- Prefer fewer, well-sourced rules over many guessed ones. A wrong hard rule
  makes a real scheme invisible, which is worse than showing one extra.

---

## HOW TO CHECK YOUR OWN WORK

Run the validator before you hand anything back:

```bash
python3 validate_bank.py question_bank.yaml attribute_seed.sql
```

It must print `OK`. It checks: acyclicity, single root, no orphan attribute
references, reserved keys never reused, max eight options per node, `collect.speech`
true everywhere, every question carries a `why` and a `say_key`, and no dependency
path deeper than the ten-question cap.

If you changed `question_bank.yaml` and the validator fails, your change is wrong.
The validator is not negotiable; it is the contract between the flowchart, the
code and the audio files.

## CONSTRAINTS ON YOU

- Do not add questions to the bank. Eighty is the agreed number for this build.
- Do not change reserved keys.
- Do not add a feature that is on the parked list: SMS, consent flow, CSAT,
  caching, full multilingual, document upload, rejection prediction, notifications.
- If the flowchart implies a parked feature, log it in the conflict list and
  move on.
- Every prompt you write in Hindi should be under twenty words spoken. Audio is
  gone the moment it is said; the caller has no screen to re-read.

## PROMPT ENDS HERE
