# API keys and local setup

**Do not paste any key into a chat window, an issue, or a commit.**
Everything below goes into a local `.env` that is never committed.

```bash
cp .env.example .env
#  then fill in .env by hand
echo ".env" >> .gitignore
```

---

## What you actually need, and when

Ordered by when it blocks you. **You can build and demo most of the system
with only item 1.**

| # | Key | Needed for | Blocks the demo? | Get it from |
|---|---|---|---|---|
| 1 | `LLM_API_KEY` | Question selection + speech-to-enum | **No** - engine falls back to rank 1 | Your existing provider |
| 2 | `SARVAM_API_KEY` | Hindi TTS and STT | No - text mode works without it | dashboard.sarvam.ai |
| 3 | `TWILIO_ACCOUNT_SID` | Phone calls | No - not building telephony tonight | console.twilio.com |
| 4 | `TWILIO_AUTH_TOKEN` | Phone calls | No | console.twilio.com |
| 5 | `TWILIO_PHONE_NUMBER` | Phone calls | No | console.twilio.com |

**Nothing here blocks tonight's build.** That is deliberate - every external
service has a working offline fallback, so a missing or rate-limited key can
never stop the demo.

---

## The fallback ladder

| Service | If the key is missing | If the call fails at runtime |
|---|---|---|
| LLM | Engine uses rank 1 from its own ranking | Same. 700ms timeout, no stall |
| Sarvam TTS | Prints the text instead of speaking | Falls back to printing |
| Sarvam STT | Speech input disabled, buttons only | Treated as "unclear", drops to buttons |
| Twilio | Simulator mode, keys typed at a terminal | n/a |

**Test this deliberately: rename `.env` and run a full call.** It must still
complete end to end. If it does not, the fallbacks are wrong and that is a bug
worth fixing before 6 AM.

---

## Sarvam specifics

- Free tier is enough for a demo, but it is rate limited. **Cache every audio
  clip to disk on first generation** and reuse it. All 81 prompts are static,
  so after one warm-up run you make almost no TTS calls at all.
- Phone audio is 8 kHz narrowband. TTS that sounds clean on a laptop can turn
  to mush on a handset. Test on an actual phone early.
- Language codes: `hi-IN`, `en-IN`.

## Twilio specifics

- **Trial accounts can only call verified numbers.** Verify every demo handset
  now, not at 5 AM.
- Trial calls get a recorded preamble before your audio. Expected, not a bug.
- Webhooks need a public URL. `ngrok http 8000` and paste the HTTPS URL into
  the number's voice webhook.

---

## Everything else runs locally

- SQLite - one file on disk, no server
- Question bank - a YAML file
- The engine - plain Python, no network

So the whole narrowing system works on a plane with no internet. Only voice
and telephony reach outside.
