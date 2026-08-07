# future/

Things that are real, decided, and deliberately not being built right now.

This folder exists so that "we'll deal with it later" has an actual address
instead of evaporating at 4 AM.

| File | What it holds |
|---|---|
| `01_BEFORE_DEMO.md` | Must happen before we show anyone |
| `02_TELEPHONY_CHECKS.md` | Twilio and audio checks, in order |
| `03_WAITING_ON.md` | Blocked on someone else |
| `04_PARKED.md` | Good ideas, wrong week |
| `05_KNOWN_RISKS.md` | What can hurt us and what we do about it |
| `06_DISTRICT_MAPPING.md` | Correct long-term, LOW value now (see CHANGE_REPORT s3) |

---

## If you only read one

`../CHANGE_REPORT.md`, then `05_KNOWN_RISKS.md`.

District mapping was demoted: 62 of 100 schemes are Puducherry, so location barely discriminates yet.

State is the strongest filter we have - most schemes are state-scoped, so it
cuts the catalogue more than any other single attribute. It is also the
question callers answer worst, because people think in districts and villages,
not states, and reading out 28 options is a bad question however it is worded.

District mapping fixes the most valuable question in the system. A caller-ID
prefix prior can go further and turn it from an open question into a yes or no.

It is a CSV and a join. It just is not tonight's work.
