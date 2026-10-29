# Hackathon requirements vs MuseCut

Source: Official Rules (cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver) and the event page, read 2026-10-29.

## Hard requirements

| Requirement | Rule text (short) | MuseCut status |
|---|---|---|
| Deadline | Nov 4, 9:00 PM ET = **Nov 5, 09:00 GMT+7** | Internal target Nov 4 21:00 GMT+7 |
| Where to submit | https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit (login required) | Form fields not seen yet: needs the participant's login |
| One track per participant / per project | "may apply for one (1) track only" | Startup track |
| Startup track eligibility | "technical founders building a product"; Representative "must be an active employee of the startup" | Representative = founder of Synthvale (confirm) |
| Use Meta AI products | Muse Spark, Glimmer, SAM 3, other Meta AI products on the event page | Muse Voice Transcribe, Muse Spark 1.3, SAM 3.1, Muse Image |
| Original work, no IP violation | Submission is own work and "does not violate ... copyright" | Code is original; demo input is Public Domain Mark 1.0 (credited); music generated in-repo (CC0) |
| Open source allowed | if licenses are followed | FastAPI, FFmpeg, Pillow, etc. |
| Credits | $150 Meta Model API credits, 10-day window | ~$0.55 per short; about $4 spent so far |

## Judging criteria (weights from the Official Rules)

| Criterion | Weight | What judges look for | MuseCut evidence | Gap / next step |
|---|---|---|---|---|
| **Impact** | 30% | real-world potential, who benefits, could people use it, **fits a problem statement** | Theme *Build for Your World: free up time*. Measured: 4.5 min (machine 3.1 + review 1.4) vs 120 min by hand (creator's estimate) on the same video | Demo uses a public-domain lecture, **not a real creator's video**. Run on the founder's own video, with a real (not scripted) review time |
| **Use of Meta AI tooling** | 25% | creative use, beyond basic integration, **unexpected capabilities** | 4 models chained; SAM 3.1 concept prompt "face" drives a reframe with no face model; Muse Image edits the real frame keeping identity; Spark picks phrase IDs (cannot invent timestamps) | **Done:** SAM 3.1 video mode (stable object ids, 5 fps, 8 requests vs ~180, ~$0.09 vs ~$0.45 on the demo video). Next candidates: Muse Voice diarization for multi-speaker; Spark vision to choose cutaways |
| **Technical depth & execution** | 20% | sound engineering, refinement, craft | JSON Schema + semantic validation with Spark retry; approve-gated render (409 after approve); background jobs with states; network retry with backoff; cost + time log; 12 unit tests | No CI; no tests for the API endpoints |
| **Demo** | 25% | working, impressive demo; **holds up live**; **quality of the demo video** | ~5-min video: explainers from real run data, guided UI walkthrough, final short, cost + impact cards | **No voice-over**; length limit unknown (check submit form); show the live app on a creator's own video |

## Open questions (need the participant)

1. Submit-form fields and demo video length/host (visible only after login).
2. Founder's own English video (8–15 min) for the real-creator demo.
3. Confirm the Startup-track Representative details (Synthvale).
