# Necromancer

One voice, three spirits. A real-time voice agent that "speaks with the dead": a bankrupt company's email archive, a vanished internet, and a dying power grid.

Built for **Kiroween 2026**, Frankenstein category, with Kiro (specs, steering files, hooks). The `.kiro/` directory at the repo root holds the specs, steering and hooks used to build it.

## The three spirits

| Spirit | Stitched to | Status |
|---|---|---|
| Enron Ghost | CMU Enron email dataset, indexed in SQLite FTS5 | Search tools and ingestion pipeline built and tested |
| Wayback Medium | Wayback Machine CDX and raw `id_` snapshot APIs | Tools with offline cache built and tested; no site cached yet |
| Grid Ghost | EskomSePush schedule data, plus a replay mode | Replay tools built against a **synthetic** fixture; live API not implemented |

The shared voice engine (Amazon Nova Sonic on Amazon Bedrock, `voice/`) and the Enron Ghost by voice are built. A live run with typed questions and the real Enron index passed (see Voice below). Microphone input, speaker playback and mascot rendering in a browser have not been tested by a person yet. Wayback Medium and Grid Ghost are not wired into the voice engine. See `.kiro/specs/necromancer.md` for the task list.

## Grounding rule

A spirit may only say what a tool returned, and every claim names its source: sender, date and a verbatim quote for emails; archived URL and 14-digit timestamp for snapshots; stage, area and start time exactly as given for outages. If a tool returns nothing, the spirit says "The trail goes cold." Full rules: `.kiro/steering/grounding.md`.

**Replay data is synthetic.** `data/fixtures/grid_replay.json` contains invented outage events for demonstration. It is labeled as such in the file and is not real Eskom or EskomSePush data. Any demo that uses it must say it is a replay.

## Mascot

A 2D white ghost, "The Little Necromancer". The mascot is 2D for the hackathon; 3D is deferred until after submission. Appearance is a declarative JSON config, kept separate from spirit logic and evidence.

## Code provenance and disclosure

- **Written for this project during the submission period:** everything in this repository, unless listed below.
- **Reused from another project:** nothing. The voice session in `voice/` was written fresh for this project from the AWS Nova Sonic documentation and `scripts/nova_smoke_test.py`. Nothing was copied from *Jozi Buddy*.
- **Third-party data:** the [CMU Enron Email Dataset](https://www.cs.cmu.edu/~enron/) (May 7, 2015 version). It is not included in this repository. See the privacy note below.
- **Mascot artwork:** the reference images in the repo root. **TO CONFIRM:** record how they were made (tool, author, terms of use) before submission.

## Wayback demo snapshot provenance
- **Page:** `http://www.pets.com/` (Pets.com home page, June 2000), served by the Wayback Machine at timestamp `20000617112828` (requested and served timestamps were identical).
- **How it was chosen:** `scripts/prewarm_wayback.py --probe` made one capture listing and one or two reads per candidate (pets.com 1999 and 2000, altavista.com 1997 and 1998, geocities.com 1998). The pets.com June 2000 page gave short headline-and-blurb text that works as a spoken answer. The 1999 pets.com capture is mostly navigation links.
- **Cache:** `data/wayback/` is gitignored; rebuild it with `scripts/prewarm_wayback.py --site http://www.pets.com/ --timestamp 20000617112828`.
- **Costume:** `mascot/costumes/wayback.json` takes its palette from this snapshot's HTML colours and records the same URL and timestamp.

## Privacy note on the Enron data

The dataset was made public during a federal investigation and contains real people's email. Many of the people in it were not involved in any wrongdoing. The CMU distributor asks users to be sensitive to their privacy. This project quotes emails only as historical evidence, and the Enron Ghost should focus on the executives and events in the public record.

## Setup

Requirements:
- Python 3.10+ with SQLite FTS5 for the spirit tools, mascot and tests (standard library only).
- **Python 3.12+** for the voice engine: the Bedrock bidirectional-streaming SDK does not support 3.10. With [uv](https://docs.astral.sh/uv/): `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`.
- Node for the UI tests (developed with v24).
- AWS credentials with access to Amazon Nova Sonic in **us-east-1**. The model is not offered in all regions (it was not listed in eu-west-1).

Check your Bedrock access with a short real call (no microphone needed):

```bash
aws login
.venv/bin/python scripts/nova_smoke_test.py
```

## Voice (Enron Ghost)
The voice server needs `aiohttp` (pinned in `requirements.txt`), AWS credentials for Nova Sonic in us-east-1 (`aws login`), and the Enron index built as shown below.
```bash
.venv/bin/python -m voice.server --spirit enron --db data/enron/enron.sqlite --port 8765
```
Open the URL it prints (`/ui/index.html?ws=ws://127.0.0.1:8765/events&io=ws://127.0.0.1:8765/io`). The server binds 127.0.0.1 only, serves only `ui/` and the mascot assets, and refuses WebSocket connections from another origin. The page has a microphone button and a text box. No Bedrock stream is open at server start. In local mode the stream opens when the first browser connects to `/io`; with `--public` it opens only after the page sends a `start` message. Each live session counts against a daily and a total cap, ends at the session limit, after idle time, or when the last browser disconnects, and appends one line (token counts only, no text) to the usage log.
Guardrail settings (flag, or environment variable when the flag is absent): `--max-session-seconds` / `NECRO_MAX_SESSION_SECONDS` (default 180, at most 480), `--idle-seconds` / `NECRO_IDLE_SECONDS` (60), `--daily-cap` / `NECRO_DAILY_CAP` (20), `--total-cap` / `NECRO_TOTAL_CAP` (200), `--usage-log` / `NECRO_USAGE_LOG` (`var/usage.jsonl`), `--caps-file` / `NECRO_CAPS_FILE` (`var/caps.json`), `--demo-video-url` / `NECRO_DEMO_VIDEO_URL`. Set `NECRO_ACCESS_CODE` in the environment to require an access code on `/events` and `/io` (sent as the first WebSocket frame, never in the URL). `--public` refuses to start without that variable.
Access code design: when `NECRO_ACCESS_CODE` is set, each WebSocket first receives a `paused` event with reason `code_required`, and the browser must answer within 5 seconds with `{"type":"auth","code":"..."}` as its first frame. A wrong code gets reason `bad_code` and close code 4401; repeated failures get 4429. The server keeps only a SHA-256 digest of the code. The provisional caps (20 per day, 200 in total) are placeholders until T7.5 sets them from a measured session.
Runtime files go under `var/` (gitignored): `var/usage.jsonl` (one JSON line per live session: `ts`, `session_id`, `seconds`, `streams`, `end_reason`, and summed `usage` with `input`/`output` `speechTokens`/`textTokens`; no transcript or email text), `var/caps.json` and `var/caps.json.lock`. Read the log with `tail -n 5 var/usage.jsonl`. The same numbers print as one `usage:` line when a session ends.
When live voice is paused the page keeps the last transcript and evidence readable, but typed questions need a live stream, so searching is not available until voice is restarted.
Guardrail live check (one short billed session with a 20 second cap): `NECRO_LIVE=1 .venv/bin/python scripts/live_guard_check.py`.
Live check against real Bedrock and the real index (typed questions, silent microphone, writes a report with per-quote substring checks): `NECRO_LIVE=1 .venv/bin/python scripts/live_enron_e2e.py data/enron/enron.sqlite`. The offline suite never calls AWS; `tests/test_voice_live.py` runs only with `NECRO_LIVE=1`.
```bash
# Run the tests
python3 -m unittest discover -s tests -t .

# Enron data (about 2.6 GB extracted; kept out of Git)
curl -L -o data/enron/enron_mail_20150507.tar.gz https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz
python3 -m spirits.enron.extract data/enron/enron_mail_20150507.tar.gz data/enron
python3 -m spirits.enron.ingest data/enron/maildir data/enron/enron.sqlite \
    --failure-log data/enron/enron_failures.log
```

Indexing takes about 12 minutes and produces a roughly 1 GB database. The full run indexed 254,364 unique emails from 517,401 files; many messages are filed in several folders and are deduplicated, with every original path kept.

## Configuration

Copy `.env.example` to `.env` and fill in values. `.env` is gitignored. Never commit credentials.

## License

TO DECIDE: add a LICENSE file (the hackathon requires an open-source repository).

Note on the wrong-code lockout: it is keyed on the remote address (5 failures per minute) plus a global limit (50 per minute). Behind a reverse proxy every client shares one remote address, so the global limit is the real guard, and an attacker can lock out legitimate visitors for up to a minute at a time.
