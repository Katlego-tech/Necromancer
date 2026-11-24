# Necromancer — Feature Specification

## 1. Requirements

### Functional requirements

#### FR-1: Shared voice engine
- The app opens a bidirectional Amazon Nova 2.5 Sonic (Bedrock) audio session.
- A `summon(spirit)` call swaps the persona prompt and tool set without dropping the audio connection.
- The transcript and evidence panel update in real time during every turn.
- Each tool result exposes `source`, `content`, and `summary` fields.

#### FR-2: Enron Ghost
- `search_emails(query)` returns ranked email matches from an FTS5 index of the CMU Enron dataset.
- `read_email(message_id)` returns the full headers and body of a specific email.
- `who_wrote_to(person, date_range)` returns all emails sent to or from that person in the range.
- Every spoken claim includes sender, date, and a verbatim quote from the retrieved email.
- If no matching emails are found, the spirit says "The trail goes cold."

#### FR-3: Wayback Medium
- `list_snapshots(url, year)` returns available CDX snapshot records for a URL and year.
- `read_snapshot(url, timestamp)` fetches and returns the text content of a specific snapshot.
- The spirit speaks in character from the archived page content, citing snapshot URL and timestamp.
- At least one curated historical website transformation is available as a demo preset.
- Fetched snapshots are cached locally to survive rate limits and network issues.

#### FR-4: Grid Ghost
- `current_stage()` returns the current load-shedding stage and start time.
- `next_outage(area)` returns the next scheduled outage for a given area code.
- `replay_outage(fixture_id)` returns a pre-recorded outage fixture for demo purposes.
- The Grid Ghost can inject an alert turn into the voice stream without a user prompt.
- All time and stage values are reported verbatim from the data source, never rounded or guessed.

#### FR-5: 2D Mascot
- A white ghost sprite with idle, listening, speaking, summoning, and alert animation states.
- Spirit summons change the costume via a declarative JSON appearance config (no prompt changes).
- Transitions never obscure the evidence panel during active evidence display.
- `prefers-reduced-motion` is respected; surprise sounds are disabled by default.
- At least two spirit appearances swap without reloading the voice session.

#### FR-6: Evidence panel
- A panel adjacent to the transcript always shows the raw source of the last cited claim.
- For emails: displays sender, date, subject, and the quoted passage.
- For snapshots: displays the archived URL, timestamp, and a text excerpt.
- For grid data: displays stage, area, and scheduled time exactly as returned by the tool.

### Non-functional requirements

#### NFR-1: Grounding
No spirit may assert a fact not returned by a tool. Grounding rules are defined in
`.kiro/steering/grounding.md` and enforced by tests.

#### NFR-2: Latency
The spirit acknowledges tool calls within 2 seconds of receiving a question (a spoken filler phrase).
Full tool-call round trips should complete within 10 seconds for cached data.

#### NFR-3: Demo reliability
All demo paths (the five canonical demo questions) must work with locally cached data.
No demo path depends on a live external API that could fail on recording day.

#### NFR-4: Repository
The `.kiro/` directory is committed at the repo root.
No secrets or credentials appear in Git history.
Reused code from Jozi Buddy is disclosed in the README.

---

## 2. Design

### Architecture overview

```
Browser UI
├── Mascot component      (2D sprite, CSS animation, appearance engine)
├── Transcript panel      (rolling live turns)
└── Evidence panel        (raw source document)
        │  WebSocket / WebRTC
        ▼
Backend (Python or Node.js — TBD)
├── Voice session manager (Nova Sonic bidirectional stream)
├── Summon controller     (swaps persona + tools)
├── Spirit router
│   ├── EnronSpirit       (FTS5 search, email reader)
│   ├── WaybackSpirit     (CDX lookup, snapshot fetch, local cache)
│   └── GridSpirit        (EskomSePush API + replay fixture)
└── Appearance emitter    (emits appearance-config events to UI)
```

### Module boundaries

**Voice engine** (`voice/`): manages the Bedrock stream, turn sequencing, filler phrases, and
barge-in handling. It knows nothing about spirits.

**Spirit modules** (`spirits/enron/`, `spirits/wayback/`, `spirits/grid/`): implement the tool
functions and the persona prompt string. They know nothing about the voice stream.

**Appearance engine** (`mascot/`): reads appearance JSON configs and drives the 2D sprite. It
knows nothing about spirit logic.

**UI** (`ui/`): displays the transcript, evidence panel, and mascot. It receives events from the
backend over a WebSocket; it does not contain business logic.

### Key data flows

**User asks Enron Ghost a question:**
1. Nova Sonic transcribes speech → emits user turn text.
2. Spirit router sends turn + Enron tools to Nova Sonic.
3. Nova Sonic calls `search_emails`; spirit says filler phrase.
4. Tool returns `{ source, content, summary }`.
5. Spirit speaks `summary`; evidence panel shows `content`.
6. Appearance emitter sends `{ spirit: "enron", animation: "summon" }` to UI.

**User summons a spirit:**
1. User says "Summon the Wayback Medium."
2. `summon("wayback")` swaps prompt and tools; emits `{ spirit: "wayback", animation: "shapeshifting" }`.
3. Mascot crossfades to Wayback costume; voice session remains live.

**Grid Ghost proactive alert (replay mode):**
1. Replay fixture triggers on a timer or manual button press.
2. Backend injects an alert message into the Nova Sonic stream.
3. Spirit speaks the alert; evidence panel shows the fixture entry.
4. Mascot enters `alert` state.

### Technology choices

| Component | Choice | Rationale |
|---|---|---|
| Voice model | Amazon Nova 2.5 Sonic (Bedrock) | Required for hackathon; Nova Sonic supports bidirectional streaming |
| Email index | SQLite FTS5 | Ships with Python; no server required; handles 500k emails well |
| Wayback API | Wayback Machine CDX + `id_` raw endpoints | Official, free, documented |
| Grid API | EskomSePush REST API + local JSON fixture | Free tier; replay protects the demo |
| Mascot rendering | CSS transforms + sprite swap | Runs in any browser; no WebGL required for 2D MVP |
| Backend language | Python | Existing Jozi Buddy voice core; boto3 is first-class for Bedrock |
| Frontend | Vanilla JS or React (minimal) | Keep dependencies lean for a demo project |

---

## 3. Implementation tasks

### Milestone 0 — Foundation (Days 1–2: 18–19 Nov)
- [x] **T0.1** Create public GitHub repository; commit `.kiro/` directory.
- [x] **T0.2** Create `.gitignore` (exclude `data/enron/`, `data/wayback/`, `.env`, `__pycache__`, `node_modules`).
- [x] **T0.3** Write `README.md` with project description, setup instructions, and Jozi Buddy voice-core disclosure.
- [x] **T0.4** Confirm `sqlite3` FTS5 support: `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`.
- [x] **T0.5** Bedrock Nova Sonic access (21 Nov 2026): `amazon.nova-2-5-sonic` is ACTIVE, AUTHORIZED, region-available in **us-east-1 only** (not eu-west-1). A real bidirectional stream has NOT been tried; no boto3 / pip / python3-venv on this machine, so the SDK install is still to do.
- [x] **T0.6** (user reports credits already granted) Request Kiro credits (deadline 1 Dec 2026).

### Milestone 1 — Enron data pipeline (Days 2–3: 19–20 Nov)
- [x] **T1.1** Download the CMU Enron email dataset (~2.6 GB) to `data/enron/` (gitignored).
- [x] **T1.2** `spirits/enron/extract.py` (safe extraction) and `spirits/enron/ingest.py` (maildir -> SQLite FTS5; evidence_id, source_path, content-hash dedupe, failure log, `--limit`, `--retry`).
- [x] **T1.3** Write `spirits/enron/tools.py`: implement `search_emails`, `read_email`, `who_wrote_to`.
- [x] **T1.4** Write unit tests for all three Enron tools in `tests/test_enron_tools.py`.
- [x] **T1.5** Enron demo questions checked against the real index (20 Nov 2026). Findings: Raptor questions return specific, cited emails (407 mention Raptor). Skilling, Lay and Fastow have almost no *sent* mail in the dataset (66 / 21 / 8), so ask what was sent *to* them (`recipient` filter) and never phrase answers as "Lay said". Keyword search matches word stems, so "losses" also returns unrelated "loss" emails; the voice prompt must read the excerpt, not assume relevance.

### Milestone 2 — Voice core + Enron ghost end-to-end (Days 4–6: 21–23 Nov)
- [ ] **T2.1** Port or adapt the Nova Sonic bidirectional session from Jozi Buddy into `voice/session.py`.
- [ ] **T2.2** Implement `voice/summon.py`: `summon(spirit)` swaps persona prompt and tool set.
- [ ] **T2.3** Wire Enron tools into the session; test a voice question end-to-end.
- [ ] **T2.4** Add transcript and evidence-panel event emitters to the voice session.
- [ ] **T2.5** Build the minimal web UI: static HTML with transcript and evidence panel (no mascot yet).
- [ ] **T2.6** Confirm grounding rule holds: spoken output cites sender, date, and verbatim quote.

### Milestone 3 — 2D Mascot (Days 7–8: 24–25 Nov)
- [ ] **T3.1** Export transparent-background base sprite from reference images (or trace manually): `assets/sprites/idle.png`.
- [ ] **T3.2** Produce `blink.png`, `talk_open.png`, `talk_closed.png`, `summon.png` from the same character.
- [ ] **T3.3** Build `mascot/appearance.js` (or `.py` server-side): reads appearance JSON config, applies CSS classes and sprite swaps.
- [ ] **T3.4** Connect mascot state to voice session events: idle → listening → speaking → summoning.
- [ ] **T3.5** Add Enron costume JSON: `costumes/enron/theme.json`.
- [ ] **T3.6** Test that mascot transitions do not obscure the evidence panel.

### Milestone 4 — Wayback Medium (Days 9–11: 26–28 Nov)
- [x] **T4.1** Implement `spirits/wayback/tools.py`: `list_snapshots` and `read_snapshot` using CDX API.
- [ ] **T4.2** Build local snapshot cache: download and store one curated historical site (e.g., pets.com 1999).
- [ ] **T4.3** Write `spirits/wayback/persona.py`: Wayback Medium persona prompt.
- [ ] **T4.4** Add `summon("wayback")` path; test a voice question end-to-end.
- [ ] **T4.5** Add Wayback costume JSON and browser-trim overlay sprite: `costumes/wayback/retro_1999.json`.
- [x] **T4.6** Write unit tests for Wayback tools; verify cached-only paths work offline.

### Milestone 5 — Grid Ghost (Days 12–13: 29–30 Nov)
- [x] **T5.1** Create `data/fixtures/grid_replay.json`: a realistic past outage record.
- [x] **T5.2** Implement `spirits/grid/tools.py`: `replay_outage`, `current_stage`, `next_outage`.
- [ ] **T5.3** Implement proactive alert injection into the Nova Sonic stream (replay mode first).
- [ ] **T5.4** Add Grid Ghost costume JSON: `costumes/grid/amber_alert.json`.
- [ ] **T5.5** Obtain EskomSePush API key; test `current_stage` live (bonus; do not block demo on this).
- [x] **T5.6** Write unit tests for Grid tools; confirm replay works offline.

### Milestone 6 — Demo polish and submission (Days 14–17: 1–4 Dec)
- [ ] **T6.1** Run the full three-minute demo script end-to-end; fix any failures.
- [ ] **T6.2** Record the demo video (under 3 minutes, YouTube).
- [ ] **T6.3** Polish README: description, setup, Kiro story, disclosure, demo video link.
- [ ] **T6.4** Verify `.kiro/` is committed and all secrets are excluded from Git history.
- [ ] **T6.5** Submit on Devpost before 4 December 2026, 2:00 PM PT.

---

## 4. Cut lines

| If behind on | Cut this | Keep this |
|---|---|---|
| Milestone 1–2 | Everything | Enron Ghost + voice core. Never cut. |
| Milestone 3 | Animated states; use static sprite | Evidence panel and transcript |
| Milestone 4 | Wayback Medium entirely | Enron Ghost |
| Milestone 5 | Grid Ghost entirely | Enron + Wayback |
| Milestone 5 | Live ESP API | Replay fixture |
| Milestone 3 | Calendar costumes | Base costume per spirit |

---

## 5. Canonical demo questions

These five questions must work with locally cached data before recording day.

1. "What did executives say about the Raptor transactions?" (Enron)
2. "What did Ken Lay know about the losses in 2001?" (Enron)
3. "Summon pets.com in 1999. What were you selling?" (Wayback)
4. "What was the front page of AltaVista in 1997?" (Wayback)
5. *(Grid replay button)* — trigger a past Stage 4 outage alert. (Grid)
