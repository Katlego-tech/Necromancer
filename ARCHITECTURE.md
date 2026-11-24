# Necromancer — Architecture Summary

*Produced 19 November 2026 by Kiro during project initialization.*
*Authority: `Necromancer_2D_First_Shapeshifter_Plan.docx` v2.0 (19 Nov 2026).*

---

## What we have

| Asset | Notes |
|---|---|
| `IDEA.md` | Original concept, hackathon facts, 17-day plan, risks |
| `FORM-ANSWERS.md` | Drafted Devpost submission answers |
| `Necromancer_2D_First_Shapeshifter_Plan.docx` | **Authoritative mascot plan** — 2D-first, MVP spec, appearance engine design |
| `Necromancer_3D_Mascot_and_Shapeshifter_Plan.docx` | Reference for post-hackathon 3D upgrade only |
| Mascot PNG references (×6, two byte-identical duplicates; none has an alpha channel) | Concept art: white ghost with cyan eyes, stitched hood, spectral tail |

---

## Document reconciliation

The two Word documents are not in conflict; they are sequential plans. Version 1.0 (3D plan) was
written first, then superseded by Version 2.0 (2D-first plan) on the same day. All functional
requirements from the original IDEA.md are preserved. The only change is the mascot rendering
strategy: 2D sprites replace 3D mesh for the December 2026 MVP, with a clear upgrade path afterward.

The original IDEA.md spirit requirements, grounding rules, demo script, cut order, and submission
deadline are unchanged and carried forward into the spec and steering files.

---

## Recommended architecture

### Why this shape

The simplest architecture that can win the hackathon:

- **One Python backend** handles Bedrock streaming, spirit routing, and tool calls.
  Python has first-class boto3 support for Bedrock, and an existing voice core (Jozi Buddy) can
  be adapted. No polyglot complexity.
- **One web UI** (minimal React or vanilla JS) shows the mascot, transcript, and evidence panel.
  The mascot is 2D sprites driven by CSS — no WebGL, no canvas, no extra build tooling needed.
- **SQLite FTS5** for Enron. It ships with Python, handles 500k emails, and needs no server.
- **Local snapshot cache** for Wayback. CDX metadata stays fresh; raw snapshots are pre-downloaded
  for the five demo questions.
- **JSON fixture** for Grid Ghost replay. The live EskomSePush API is a bonus, not a dependency.

```
Browser
┌──────────────────────────────────────────────┐
│  Mascot (CSS + sprite swap)                  │
│  Transcript panel   │   Evidence panel       │
└──────────────────────────────────────────────┘
              │  WebSocket
┌──────────────────────────────────────────────┐
│  Python backend (FastAPI or aiohttp)         │
│  ┌────────────────────────────────────────┐  │
│  │  voice/session.py                      │  │
│  │   Nova Sonic bidirectional stream      │  │
│  │   turn sequencing, filler phrases      │  │
│  └───────────────┬────────────────────────┘  │
│                  │ summon(spirit)             │
│  ┌───────────────▼────────────────────────┐  │
│  │  voice/summon.py                       │  │
│  │   swaps persona prompt + tool set      │  │
│  └──────┬──────────┬──────────┬───────────┘  │
│         ▼          ▼          ▼              │
│  spirits/enron/ spirits/wayback/ spirits/grid/ │
│  FTS5 SQLite   CDX + cache   replay + ESP API  │
│                                               │
│  mascot/appearance.py                        │
│   emits appearance-config events to UI       │
└──────────────────────────────────────────────┘
              │  AWS SDK (boto3)
         Amazon Bedrock
         Nova 2.5 Sonic
```

### Directory layout

```
necromancer/              ← repo root
├── .kiro/                ← committed (hackathon requirement)
│   ├── steering/
│   ├── specs/
│   └── hooks/
├── voice/
│   ├── session.py        ← Bedrock bidirectional stream
│   └── summon.py         ← spirit swap logic
├── spirits/
│   ├── enron/
│   │   ├── tools.py      ← search_emails, read_email, who_wrote_to
│   │   └── persona.py    ← Enron Ghost system prompt
│   ├── wayback/
│   │   ├── tools.py      ← list_snapshots, read_snapshot
│   │   └── persona.py
│   └── grid/
│       ├── tools.py      ← next_outage, current_stage, replay_outage
│       └── persona.py
├── mascot/
│   ├── appearance.py     ← server-side appearance event emitter
│   ├── assets/
│   │   └── sprites/      ← idle, blink, talk_open, talk_closed, summon
│   └── costumes/
│       ├── enron/theme.json
│       ├── wayback/retro_1999.json
│       └── grid/amber_alert.json
├── ui/
│   ├── index.html
│   ├── mascot.js         ← sprite/CSS animation driver
│   ├── transcript.js
│   └── evidence.js
├── data/
│   ├── enron/            ← gitignored; download separately
│   ├── wayback/          ← gitignored; cached snapshots
│   └── fixtures/
│       └── grid_replay.json
├── tests/
│   ├── test_enron_tools.py
│   ├── test_wayback_tools.py
│   ├── test_grid_tools.py
│   └── test_grounding.py ← grounding rule enforcement
├── scripts/
│   └── index_enron.py    ← one-time ETL: maildir → SQLite FTS5
├── .env.example          ← documents required env vars; .env is gitignored
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Missing prerequisites to resolve before coding

| Item | Action |
|---|---|
| AWS account + Bedrock Nova Sonic access | Verify model availability; request Kiro credits by 1 Dec |
| EskomSePush API key | Sign up at eskomsepush.com (free tier) |
| Enron dataset download | `wget https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz` |
| Public GitHub repo | Create and push `.kiro/` on Day 1 |
| Country eligibility | Confirm South Africa is eligible on Devpost official rules |

---

## Immediate next steps

1. **Create the GitHub repo** and push this initial `.kiro/` structure.
2. **Confirm SQLite FTS5** (`python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`).
3. **Confirm Bedrock access** (`aws bedrock list-foundation-models --region us-east-1`).
4. **Download the Enron dataset** to `data/enron/` (gitignored).
5. Begin **Milestone 1** (Enron data pipeline) in the spec.
