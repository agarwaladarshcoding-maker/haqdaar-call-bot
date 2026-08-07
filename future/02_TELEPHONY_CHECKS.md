# Telephony reality checks

Your list - the things that have nothing to do with design but will eat hours.
Do these **early**, not at 5 AM.

## Does the call even reach the phone
- [ ] Twilio trial numbers only dial **verified** numbers. Verify every handset
      you plan to demo on, **now**. This bites people at 5 AM.
- [ ] Test on a real mobile network, not just a softphone on wifi
- [ ] Test one incoming call and one outgoing

## Voice quality
- [ ] Is the TTS actually intelligible over a phone codec? Phone audio is
      8kHz and narrowband - TTS that sounds fine on a laptop can turn to mush.
- [ ] Are Hindi numbers spoken correctly? "1" vs "ek" vs "one" is a common mess.
- [ ] Volume consistent between pre-recorded audio and live TTS. If some
      prompts are recorded and some are generated, they will not match.
- [ ] Any clipping at the start of a clip? Add 200ms of silence padding.

## Timing
- [ ] How long from dial to first audio? Over 3 seconds feels broken.
- [ ] How long does our server take to reply to a webhook? If it is over
      about a second, Twilio has already moved on. **Pre-generate audio.**
- [ ] LLM call latency measured on the actual network, not localhost

## Barge-in
- [ ] Can the caller press a key while audio is playing, or do they have to
      wait? They must not have to wait.
- [ ] Does `*` interrupt mid-sentence?

## What you can see while it runs
- [ ] Terminal prints, per turn: question asked, key received, scheme count
      remaining. **You need to see the count dropping** - that is the demo.
- [ ] Errors go to the terminal, not swallowed
- [ ] A transcript of the call printed at the end, for the deck

## Fallback if telephony fights back
Have a **local simulator** that runs the exact same engine over the terminal -
type a key, see the response. If Twilio dies at 5:30 AM you demo the simulator
and the logic is identical. This is worth 20 minutes and it has saved many demos.
