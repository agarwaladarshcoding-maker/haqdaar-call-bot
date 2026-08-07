# Must work by 6:00 AM

Tick these off. Nothing else matters tonight.

## The call has to happen
- [ ] Twilio number answers
- [ ] Greeting audio plays and is audible on a real handset
- [ ] A keypress is received by our server
- [ ] The call can end cleanly without an error

## The loop has to run
- [ ] Language question, then intent question
- [ ] At least 5 narrowing questions asked in sequence
- [ ] Scheme count visibly drops as answers come in
- [ ] Loop stops at 5 or fewer schemes
- [ ] At least 2 scheme names read out at the end

## The three keys have to work
- [ ] `0` returns to language selection
- [ ] `*` repeats what was just said
- [ ] `#` undoes the last answer **and the scheme count goes back up**

## Data
- [ ] 100 schemes loaded
- [ ] At least 20 of them carry real eligibility rules
- [ ] 6 schemes hand-checked so the demo path is guaranteed correct

## Not breaking on stage
- [ ] LLM timeout falls back to rank 1 and the call continues
- [ ] Silence for 30s ends the call politely, no crash
- [ ] Three wrong keys does not loop forever

---

## Cut list, in this order, if we run out of time

1. Speech input. **Buttons only.** The Gujarat IVR study says callers prefer
   keys anyway, and it removes an entire class of live failure.
2. English prompts. Hindi only.
3. LLM question selection. Use rank 1 every time - the ranking is deterministic
   and the demo still narrows correctly.
4. The `0` main-menu key. Rarely pressed in a scripted demo.

**Never cut:** `*` repeat and `#` back. Those are the two a judge will press.
