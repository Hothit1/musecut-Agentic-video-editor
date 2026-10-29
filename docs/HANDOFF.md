# Handoff

- updated_at: 2026-10-29T12:00:00+07:00 · machine: Macbook-bros-MacBook-Pro (macbro) · agent: Claude Code
- branch: main (local only, no remote yet)
- objective: MuseCut v1 for Meta hackathon (deadline 2026-11-05 09:00 VN)
- done: full pipeline (Muse Voice Transcribe → Spark 1.3 plan → SAM 3.1 crop → Muse Image thumbs/B-roll → FFmpeg render), plan editor UI, approve lock, export, cost log, 12 unit tests, e2e run on 15-min Citino lecture, demo recording scripts.
- decisions: Meta models for every step (no Whisper) → phrase-level timestamps, word timing estimated; captions drawn with Pillow (local FFmpeg lacks libass/drawtext); music generated procedurally (CC0).
- remaining: create GitHub repo + push; record voice-over / final demo cut; read submission format in Official Rules.
- env_revision: 1 · shared env files: `.env` (META_API_KEY, META_API_BASE_URL, SPARK_MODEL_ID, MUSE_IMAGE_MODEL_ID)
