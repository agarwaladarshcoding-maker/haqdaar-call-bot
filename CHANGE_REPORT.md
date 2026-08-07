# Change Report - v4 (KisanSetu) to v5 (Haqdaar)

Written after loading the real 100-scheme catalogue. Everything below changed
because the data contradicted an earlier assumption, not because of a preference.

**Read this before anything else in the folder.** Two files here (`PRD.md` and
`SYSTEM_DESIGN.md`) are loaded into Antigravity's base context, so these
corrections propagate straight into the coding agent.

---

## 1. Rename: KisanSetu to Haqdaar

**Why.** The product is not farmer-specific and the data proves it. Calling it
"Kisan" anything would promise a farmer service and deliver a business subsidy
service.

**Done.** All files renamed, database is `haqdaar.db`, zero residual
`kisansetu` references (verified by grep). Positioning is now "schemes for
whoever you are" rather than any one group.

---

## 2. The catalogue is not what the design assumed

I built v1 to v4 around a farmer caller. The data says otherwise:

| Theme | Schemes |
|---|---|
| Business, industry, shop | 52 |
| Handloom, coir, handicraft | 13 |
| Prashikshan / rozgar | 10 |
| Pension, relief, study | 10 |
| Fisheries and boats | 9 |
| Farming and horticulture | 6 |

Farming is the **smallest** bucket at 6 percent. Business is 52 percent.

**Consequence.** The six top-level problem statements I seeded in v4 (Paisa,
Karza, Fasal beema, Yantra, Pension, Pashu) were fiction. A caller picking
"Fasal beema" would have hit zero schemes, because there is no crop insurance
scheme in this catalogue at all. Replaced with the six data-derived themes above,
ordered by real volume so the commonest ask is option 1.

---

## 3. Retraction: state is NOT our strongest filter

I said this repeatedly, in the v4 chat, in `PRD.md`, and in
`future/06_DISTRICT_MAPPING.md`. **It is false for this dataset.**

| State | Schemes |
|---|---|
| Puducherry | 62 |
| West Bengal | 16 |
| No state resolved | 5 |
| Madhya Pradesh | 3 |
| Andhra Pradesh | 3 |
| Others | 11 |

62 percent are Puducherry. Asking "which state are you in" mostly confirms what
we already know - it is close to a constant, not a discriminator.

**Changes made.** State demoted from primary filter to a late-applied tiebreaker.
It still never eliminates a scheme whose `state_scope` is NULL.

**Knock-on.** `future/06_DISTRICT_MAPPING.md` was billed as the highest-value
next feature. It is still correct long-term but close to worthless on a catalogue
that is 62 percent one union territory. Demoted in `future/README.md`. Keep the
file; it becomes valuable the moment the catalogue broadens.

---

## 4. Dead attributes cut from the question bank

I tested all 17 farmer attributes against the 100 schemes. Nine were dead:

| Attribute | Schemes matched |
|---|---|
| `has_kcc`, `livestock`, `irrigation`, `tractor`, `soil_health_card`, `pmfby_insured`, `fpo_member` | 0 |
| `ration_card` | 1 |
| `aadhaar` | 2 |

Asking about a tractor spends a question and eliminates nothing. On a 10-question
budget that is expensive.

**Replaced with business and MSME attributes**, all live: `enterprise_type` 44,
`employment_created` 40, `is_registered_firm` 39, `sector` 34,
`investment_size` 27, `loan_taken` 20, `unit_stage` 17, `udyam_registered` 16,
`training_related` 13, `coop_member` 9, `commercial_production` 7,
`worker_registered` 5, `boat_owner` 5, `power_connection` 3, `location_zone` 3.

---

## 5. New early fork: person or business

`applicant_type` splits the catalogue: business 31, person 26, both 43.

This is the highest-information question available and trivially easy to answer
out loud - a caller always knows whether they are asking for themselves or for a
shop. It now runs early, ahead of everything except language.

The 43 "both" schemes are never eliminated by this question, which is the correct
conservative behaviour.

---

## 6. Business needed a second menu level

52 schemes cannot fit in one voice menu. I split them by **what the caller
needs**, not by industry sector, because a shop owner knows the electricity bill
hurts but may not know whether they count as "MSME manufacturing".

| Need group | Schemes |
|---|---|
| Machine, tools or premises | 15 |
| Tax, stamp duty or refund | 15 |
| Loan or interest relief | 7 |
| Technology, patent, quality | 6 |
| Marketing, fairs, export | 5 |
| Electricity or water | 4 |

The other five themes go straight to a scheme list - all are 13 or fewer.

---

## 7. Dial codes decoupled from menus

**v4 design:** dial code was `ps_key + slot_key + sec_key`, so the code was a
path through the menu tree. That was a mistake - reordering a menu silently
changed every code.

**v5 design:** every scheme gets a stable `scheme_no` from 1 to 100, assigned in
sorted-slug order at ingest. Dial code is `2-digit scheme_no + 1-digit section`.
Example: `021` is scheme 02, section 1 (kya milega).

Menus are for browsing. Codes are for addressing. They now change independently,
and test A8 asserts a re-run never renumbers anything.

---

## 8. Short names: mostly solved

Scheme names here are brutal - median 13 words, longest 29, and 83 of 100 exceed
8 words. Unspeakable on a phone.

I added extraction that pulls the quoted name, cuts at `under` / `component of` /
colon, and caps at 8 words. That brought the manual-rewrite pile from **83 down
to 15**. Those 15 are flagged `needs_human_name = 1` - query that column and fix
only those.

---

## 9. Testing is now mandatory per step

Per your instruction. New file `TEST_CASES.md`: 90 numbered tests across 13
groups, each as given/when/then. `BUILD_STEPS.md` now carries a `TESTS:` line on
every step naming the cases that gate it, so a step is not done until its tests
pass.

Two worth knowing by heart:

- **B1** - with no answers, narrowing must return all 100. If it returns fewer,
  the SQL is inverted and the system hides schemes from every caller.
- **C5** - pressing `#` must grow the candidate count back. If it only re-asks
  the question without restoring candidates, the call sounds perfect and the
  answer is silently wrong. Worst bug class we have.

---

## 10. What did NOT change

- Keymap: `0` main menu, `*` repeat, `#` back. Locked.
- Three authorities: SQL decides what survives, the DAG decides what is askable,
  the LLM only picks from a shortlist of 5.
- Unknown never eliminates. `NOT EXISTS (violated hard rule)`, never
  `EXISTS (satisfied)`.
- Stop at 5 candidates or 10 questions. No time cutoff.
- 30-second silence ladder, 2 speech attempts then buttons.
- Scheme text spoken byte-identical from the DB. Nothing paraphrased.
- Build the outer system first, telephony last.

---

## 11. Honest open risks

1. **Coverage is thin outside PY and WB.** A Bihar caller gets almost nothing.
   For the demo, present callers as being in a covered state, or say the coverage
   limit out loud. Do not let a judge discover it by accident.
2. **Theme classification is keyword-based.** Right often enough to demo, but not
   verified per scheme. Spot-check the 6 farming and 9 fisheries rows, since
   those buckets are small enough that one error is visible.
3. **~500 eligibility rules still need authoring** from 507 eligibility
   sentences. This is the real remaining bulk and it is human-shaped.
4. **15 scheme names still need a human.** They will sound like stubs otherwise.
5. **`verified = 0` on all 100.** No deadline may be spoken as fact until someone
   checks it. The gate is in place; the checking is not done.
