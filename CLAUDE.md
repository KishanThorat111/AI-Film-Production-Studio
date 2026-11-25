# AI Film Production Studio

This repository is PUBLIC. Scripts, films, media, notes and anything personal live in `private/` (its own local repository) or `data/`, and are never committed here. `tools/publish_check.py` runs before every push; keep `git config core.hooksPath .githooks`. Stage files by name; never `git add -A` without reading `git status` first.

Local-first studio for original horror films. Read `docs/IMPLEMENTATION.md` first, then `docs/knowledge/`.

- Fixed rules: Gemini video only by hand in the Gemini app (no API, no browser automation, no account rotation); images at most $10 and Gemini inspection at most $5 in total; ElevenLabs included credits only, nothing bought; no Cutroom, no DaVinci Resolve; keys only in the app; nothing is uploaded; the user decides canon, casting, money, rights and final approval.
- Never write to `data/` or open `data/studio.db` in development. Use a temporary `STUDIO_DATA`. Never act as the user.
- No film, person, place or shot may be named in `studio/` or `web/`.
- Schema steps in `studio/schema/` are append-only. What the engine accepts it must do. A sent request is only free when something proves it.
- Python: `.venv/Scripts/python.exe`. Tests: `-m pytest` (about 12 minutes; run the touched files first). After changing agents, gates, effects, commands or schema: `tools/knowledge.py`.
- Claude usage is scarce: no agent batches; state the estimate first.
