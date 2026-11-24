# Necromancer — Project Steering

## What this project is

Necromancer is a real-time voice agent for the Kiroween 2026 hackathon (Frankenstein category).
It stitches Amazon Nova 2.5 Sonic (real-time voice) onto three pieces of dead or dying technology.

**Submission deadline: 4 December 2026, 2:00 PM PT.**
Kiro credit request deadline: 1 December 2026.
Judging: 16 December 2026 – 29 January 2027.

## Three spirits — build in this order, cut from the bottom

| Priority | Spirit | Data source |
|---|---|---|
| Must-have | Enron Ghost | CMU Enron email dataset, indexed with SQLite FTS5 |
| Should-have | Wayback Medium | Wayback Machine CDX and raw snapshot APIs |
| Cut-first | Grid Ghost | EskomSePush API + local replay fixture |

## Repository rules

- The `.kiro/` directory must be committed at the repo root (hackathon requirement).
- Never commit credentials, API keys, or secrets. Use `.env` files excluded by `.gitignore`.
- Disclose any reused code from other projects (e.g., voice core from Jozi Buddy) in the README.
- Keep the repo public and open-source.

## Technical decisions already made

- **Voice:** Amazon Nova 2.5 Sonic via Amazon Bedrock Converse/Bidirectional streaming.
- **Email index:** SQLite FTS5 (keyword search first; embeddings only if recall is insufficient).
- **Mascot:** 2D sprite-based white ghost with layered PNG/WebP assets. No 3D for the hackathon.
- **UI:** Web-based (browser). Live transcript + evidence panel. Simple is fine.
- **Appearance engine:** declarative JSON config, renderer-agnostic (ready for 3D after launch).

## What lives in which part of the codebase

```
necromancer/
├── voice/          # Shared voice engine (Nova Sonic, summon, transcript)
├── spirits/
│   ├── enron/      # Tools: search_emails, read_email, who_wrote_to
│   ├── wayback/    # Tools: list_snapshots, read_snapshot
│   └── grid/       # Tools: next_outage, current_stage, replay_outage
├── mascot/         # 2D appearance engine + state machine
│   ├── assets/     # PNG/WebP sprites and SVG sources
│   └── costumes/   # Per-spirit and per-event JSON theme files
├── ui/             # Web front-end (transcript + evidence panel + mascot)
├── data/
│   ├── enron/      # Downloaded Enron maildir (gitignored, large)
│   └── wayback/    # Cached snapshots (gitignored)
└── tests/          # Unit and integration tests per module
```
