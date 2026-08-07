# What could break during the live demo

For each one: how likely, how bad, and what you do about it in the moment.

---

### 1. Zero schemes match mid-call
The caller answers something no scheme allows and the list empties.

- Likelihood: **medium** while rules are thin
- On stage: the line says "no exact match, here is the closest" instead of dying
- Fix now: engine undoes the last answer and picks a different question
- Also: never mark a rule `hard` unless the scheme document literally says so.
  A wrong hard rule silently hides a correct scheme.

---

### 2. The LLM is slow or down

- Likelihood: **medium**
- On stage: nobody notices - 700ms timeout, engine takes rank 1
- The ranking is deterministic, so the call is still correct, just less clever
- **Test this by unplugging the LLM entirely and running a full call**

---

### 3. Speech recognition mangles Hindi

- Likelihood: **high**
- On stage: this is the one that embarrasses you
- **Mitigation: turn speech off for the demo.** Buttons only. Mention speech as
  a roadmap item instead of risking it live.

---

### 4. Twilio trial limits

- Likelihood: **medium**
- Trial accounts only call verified numbers and prepend a trial message
- Verify every demo handset **now**
- Fallback: the terminal simulator from `02_TELEPHONY_CHECKS.md`

---

### 5. `#` back leaves stale state

- Likelihood: **high if not tested**
- Symptom: caller goes back, but the scheme count does not go back up
- This is the single most likely logic bug in the system
- **Test explicitly:** answer, note the count, press `#`, count must return to
  its previous value

---

### 6. Audio mismatch
Pre-recorded clips and live TTS at different volumes or voices.

- Likelihood: **medium**
- Sounds unprofessional even when the logic is perfect
- Pick one source for all audio if there is any doubt

---

### 7. Running out of time and merging badly

- Likelihood: **high** - this is a hackathon
- Tag a working build the moment the first end-to-end call succeeds
- Demo the tag, not the branch
- After the tag, every change must keep that call working
