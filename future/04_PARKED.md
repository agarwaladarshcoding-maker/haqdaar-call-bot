# Parked - after the hackathon

Good ideas. Not tonight. Written down so they stop taking up head space.

## Product
- SMS the scheme details after the call
- Remember the caller between calls, skip questions already answered
- Consent flow before recording anything
- Satisfaction score at the end of the call
- "Why was my application rejected" prediction
- Notify the caller when a new scheme matches their profile
- Application status lookup - needs real government API access

## Language
- More than Hindi and English. Marathi, Bhojpuri, Telugu, Bengali.
- Dialect handling. Rural Hindi is not textbook Hindi.
- Code-mixed input, which is how people actually speak

## Engineering
- Cache the narrowing result per answer-set
- Move off SQLite when schemes go past a few thousand
- Pre-generate every audio file at build time instead of live TTS
- Rate limiting and abuse protection
- Proper observability instead of terminal prints
- Load testing for concurrent calls

## Data
- Automated ingestion from myscheme.gov.in when it updates
- Confidence score per rule, and a review queue for low-confidence ones
- State-level schemes, not just central
- Deadline tracking, with the rule that a deadline is only ever spoken from a
  verified row

## Human handoff
- Warm transfer to Kisan Call Centre, 1800-180-1551, 6 AM to 10 PM
- Callback logging outside those hours
- A two-line summary handed to whoever picks up

*(Note: human transfer moved here because `0` is now the main-menu key.
There is currently no key that reaches a person. That is a real gap for
production, but it is fine for a demo.)*
