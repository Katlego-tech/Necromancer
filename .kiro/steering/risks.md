---
inclusion: manual
---

# Risks and Mitigations

## Known risks

### Search quality over 500k emails
FTS5 keyword search may miss context-heavy questions. Mitigation: start with FTS5; add Bedrock Titan
embeddings only if demo questions fail. Test against the five canonical demo questions first.

### Tool-call latency in voice
Tool calls pause the voice stream. Mitigation: the spirit says "summoning…" immediately, then
speaks the result. Keep results to one or two sentences; show detail in the evidence panel.

### Wayback Machine rate limits
CDX and snapshot APIs have rate limits and occasional failures. Mitigation: cache all snapshots
used in the demo locally. Pre-warm cached versions before recording day.

### Grid Ghost proactive alert
Injecting an agent-initiated turn into a bidirectional stream is non-trivial. Mitigation: implement
replay mode (a local fixture) before attempting the live EskomSePush API. Prove the stream injection
works in isolation before integrating into the full session.

### Load-shedding may not be active
The free EskomSePush tier limits API calls per day, and load-shedding has been intermittent.
Mitigation: replay mode is mandatory; treat live API as a bonus.

### Scope creep
Three polished spirits is too much to risk. Cut order: Grid Ghost first, then Wayback Medium polish.
One fully evidenced Enron ghost beats three half-working spirits.

### South Africa eligibility
Confirm whether South Africa is an eligible country under the official hackathon rules on Devpost.

### "Built with Kiro" requirement
The official rules likely require the project to be built during the submission period (from 30 Oct 2026).
The voice core pattern reused from Jozi Buddy must be explicitly disclosed.

## Missing prerequisites

| Item | Status | Action |
|---|---|---|
| AWS account with Bedrock Nova Sonic access | Unknown | Verify model availability; request Kiro credits by 1 Dec 2026 |
| EskomSePush API key | Not obtained | Sign up at eskomsepush.com (free tier) |
| Enron email dataset (~2.6 GB maildir) | Not downloaded | Download from CMU: https://www.cs.cmu.edu/~enron/ |
| SQLite FTS5 (ships with Python 3.8+) | Available | Confirm with `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"` |
| Node.js / Python environment decision | Open | Decide before building the voice core |
| Mascot base sprite | Not created | Draw or export from reference images in workspace |
| Public GitHub repo | Not created | Create on Day 1 |
