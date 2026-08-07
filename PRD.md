# Haqdaar Voice - PRD v2

**Supersedes:** Haqdaar-Voice-PRD-v1.1-final
**Date:** 8 Aug 2026
**Companion documents:** `SYSTEM_DESIGN.md`, `BUILD_STEPS.md`

---

## 1. Problem

India runs thousands of welfare and agriculture schemes. The people they are written for very often cannot find them.

- **~630 million** Indians are not active internet users
- **~1.7 lakh** scheme helpline calls a year go unanswered
- KCC applications fell from **58.38 lakh** (2020-21) to **35.22 lakh** (2022-23)

The portals exist. They assume a smartphone, literacy in English or formal Hindi, and the patience to read eligibility tables. Someone with a feature phone has none of those, and is precisely the person the scheme was written for. That person may be a shop owner, a weaver, a fisherman, a widow claiming a pension, or a farmer.

**A phone call is the only interface that already reaches everyone.**

---

## 2. What we are building

A voice line anyone calls and, in about four minutes, learns which schemes they can actually get and what to do next. Not farmers only: this catalogue is 52% business and shop owners, 13% weavers and artisans, 9% fisherfolk, 10% training and jobs, 10% pension and study support, 6% farming.

**Not** a chatbot. **Not** an app. A phone number.

### Principles

1. **Buttons are the contract, voice is a bonus.** Everything must be doable with a keypad. Speech only ever makes it faster.
2. **Never invent.** All scheme content is read verbatim from the database. The model never writes a benefit, a number, or a deadline.
3. **Silence is not a no.** An unanswered question never eliminates a scheme.
4. **Never a dead end.** Every failure path leads somewhere useful.
5. **Six options maximum.** Nobody remembers more than that from spoken audio.

---

## 3. Who is calling

| | |
|---|---|
| **Device** | Feature phone, 8 kHz narrowband audio |
| **Language** | Hindi, some English, often a regional accent |
| **Literacy** | Variable. Cannot assume reading |
| **Numeracy** | Reliable. Everyone can press a number |
| **Patience** | Low. Four minutes is the realistic budget |
| **Cost sensitivity** | High. The call may cost them money |

Evidence: the Gujarat IVR study (51 farmers, 7 months, CHI 2010) found numeric input beat speech consistently. UPI 123PAY serves ~400M feature-phone users across 12 languages on exactly this pattern.

**This is why buttons are the contract.**

---

## 4. The call

1. **Language** - 1 Hindi, 2 English, 3 both. Buttons only.
2. **Route** - know a scheme, find one, or have a code.
3. **Narrow** - 5 to 6 questions, 10 maximum.
4. **Present** - up to 5 matching schemes.
5. **Detail** - 5 fixed sections per scheme.
6. **Close** - what to do next.

### Global keys, fixed everywhere

| Key | Action |
|---|---|
| `0` | Main menu |
| `*` | Repeat |
| `#` | Back |

Never announced inside a menu. Taught once, at the start.

### Number tree

Three levels, five to six options each: **problem statement, then scheme, then section.** The three digits together form a direct dial code, so `231` jumps straight to a known scheme's benefits.

The five sections are identical for every scheme: what you get, who is eligible, documents, how to apply, about the scheme.

---

## 5. Requirements

### Must have

| ID | Requirement |
|---|---|
| M1 | Every interaction completable by keypad alone |
| M2 | Language: 1 Hindi, 2 English, 3 both. No voice at this step |
| M3 | Global `0` `*` `#` at every node |
| M4 | Narrowing stops at 5 or fewer candidates, or 10 questions |
| M5 | Unanswered attributes never eliminate a scheme |
| M6 | All scheme text spoken verbatim from the database |
| M7 | Unclear speech: 2 retries, then buttons |
| M8 | Wrong button: 3 tries, then a 2-option menu |
| M9 | Silence: 30s ladder, then a polite close |
| M10 | Spoken scheme names confirmed back before acting |
| M11 | Zero candidates recovers by undoing the last answer |
| M12 | Deadlines spoken only from `verified = 1` rows |
| M13 | Caller answers never persisted to disk |
| M14 | System runs fully with every external key absent |

### Should have

| ID | Requirement |
|---|---|
| S1 | Speech input at nodes other than language |
| S2 | Direct dial codes |
| S3 | Call replay from an event log |
| S4 | Cached TTS clips |

### Out of scope for the demo

Application submission, document upload, status tracking, SMS follow-up, authentication, languages beyond Hindi and English, district mapping (see `future/06`).

---

## 6. Success criteria

| Metric | Target |
|---|---|
| Hallucinated scheme names | **0 out of 10 test calls** |
| Questions to an answer | 5-6 typical, 10 hard ceiling |
| Call length | about 4 minutes |
| Containment | above 12 percent, the economic break-even |
| Completes with no API keys | Yes |

**The zero-hallucination gate is not negotiable.** Telling someone they qualify for money that does not exist is worse than telling them nothing.

---

## 7. Data

- **Source:** `shrijayan/gov_myscheme` (HuggingFace, 723 PDFs, Apache-2.0, ~69MB)
- **Backup:** `jainamgada45/indian-government-schemes` (Kaggle)
- **Demo scope:** 100 schemes
- **Verified:** 5-6 hand-checked, `verified = 1`

Each scheme carries raw source fields plus four derived ones: `state_scope`, `name_short_hi`, `benefit_one_line`, `verified`.

**Known gap:** the source has a `level` of State but no state field. State must be extracted from prose at ingest. Extraction failure leaves NULL, which means the scheme is never eliminated.

Eligibility prose becomes `scheme_rules`, typically 3-6 per scheme, marked hard or soft. Hard only when the source states an absolute bar.

---

## 8. Architecture summary

Three authorities: **SQL** decides which schemes survive. **The DAG** decides what can be asked and which buttons exist. **The LLM** picks one question from a pre-approved shortlist of five.

The engine is a pure function taking state and an event and returning new state and actions, with no knowledge of telephony. Twilio and the simulator are both just clients of it.

Full detail in `SYSTEM_DESIGN.md`.

---

## 9. Economics

| | Human helpline | Haqdaar |
|---|---|---|
| Cost per resolved call | Rs 80-130 | about Rs 10-14 |
| Annual cost at volume | Rs 33 cr | Rs 3.3 cr |

Break-even containment is about **12 percent**. Even a system that only fully handles one call in eight pays for itself, and the other seven still reach a human faster than they do today.

---

## 10. Stack

| Layer | Choice | Fallback |
|---|---|---|
| Telephony | Twilio trial | Simulator |
| TTS and STT | Sarvam | Text mode |
| Database | SQLite with FTS5 | none needed |
| Engine | Python, pure functions | none needed |
| Selector | LLM, 700ms budget | Deterministic rank 1 |

Production path: Bhashini or VoicERA for language.

---

## 11. Sequencing

Core engine and data first. Simulator third, so a demo exists from that point onward. Audio and telephony last, because they are I/O and each has a working fallback.

Steps in `BUILD_STEPS.md`.

---

## 12. Parked

CORRECTION (v5): state is NOT our strongest filter. 62 of 100 schemes are Puducherry and 16 are West Bengal, so state is close to a constant on this catalogue and mostly confirms what we already know. It is now a late-applied tiebreaker. District mapping (`future/06`) stays correct long-term but is low value until the catalogue broadens beyond two regions. The strongest real filter is applicant type (business 31 / person 26 / both 43), asked early. Remaining items in `future/04_PARKED.md`.
