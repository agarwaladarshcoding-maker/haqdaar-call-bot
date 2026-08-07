# District mapping - the biggest single upgrade after the demo

## The problem

We ask "aapka rajya kaun sa hai?" and assume the caller knows. Many do not
think in states. They think in **village, block, district**. Someone in
Bundelkhand may not immediately say "Uttar Pradesh", and asking a farmer to
pick a state from a spoken list of 28 is a bad question no matter how it is worded.

It is also our **highest-value** attribute - state alone eliminates a large
share of the catalogue, because most schemes are state-scoped.

So the most valuable question in the system is also one of the hardest to ask.

---

## The fix

Ask for the **district**, derive the state automatically.

```
caller says "Sagar"  ->  district = Sagar  ->  state = Madhya Pradesh
```

One spoken word, two attributes filled, and it is a word people actually use
about themselves.

---

## Even better: skip the question entirely

The caller's phone number carries location information before they say anything.

1. **Caller ID prefix.** Indian mobile numbers map to a telecom circle. A circle
   is roughly a state. Not exact - number portability and migration break it -
   but as a **prior** it is excellent. Pre-fill state, then confirm with one
   yes/no instead of asking an open question.
2. **Confirm, do not assume.** "Aap Madhya Pradesh se hain? Haan 1, nahi 2."
   A yes/no is worth almost as much as the open question and costs a fraction
   of the time.
3. If they say no, fall back to asking the district.

This turns the single most expensive question into the cheapest one.

---

## What it needs

| Piece | Effort | Notes |
|---|---|---|
| District to state table | small | ~780 districts. Public data, one CSV |
| District name aliases | medium | Spellings, renames, local names. Gurgaon/Gurugram, Bangalore/Bengaluru |
| Phonetic matching on spoken districts | medium | Hindi ASR will not spell them the way our table does |
| Telecom circle to state prior | small | ~22 circles, static lookup |
| `district_scope` on schemes | medium | Column already exists in `scheme_catalogue.sql` |

---

## Why it also improves narrowing

District is not only a shortcut to state. Plenty of schemes are genuinely
district-scoped - aspirational districts, drought-declared blocks, tribal
sub-plan areas, coastal fishery zones. Right now those all look like
state-level schemes to us, so we show them to people who cannot get them.

`schemes.district_scope` is already in the schema, unused, waiting for this.

---

## Rough sequence, post-hackathon

1. Load the district-to-state CSV. Derive state from district. **Half a day.**
2. Add the alias table and fuzzy matching. **One day.**
3. Add the caller-ID prefix prior and the confirm-instead-of-ask flow. **Half a day.**
4. Backfill `district_scope` on schemes that are genuinely district-limited.
   **Ongoing, data work.**

Step 1 alone is worth doing. It is a CSV and a join, and it removes the worst
question in the bank.
