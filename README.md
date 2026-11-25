# Necromancer

One voice, three spirits. A real-time voice agent that "speaks with the dead": a bankrupt company's email archive, a vanished internet, and a dying power grid.

Built for **Kiroween 2026**, Frankenstein category, with Kiro (specs, steering files, hooks). The `.kiro/` directory at the repo root holds the specs, steering and hooks used to build it.

## The three spirits

| Spirit | Stitched to | Status |
|---|---|---|
| Enron Ghost | CMU Enron email dataset, indexed in SQLite FTS5 | Search tools and ingestion pipeline built and tested |
| Wayback Medium | Wayback Machine CDX and raw `id_` snapshot APIs | Tools, persona, costume and one cached snapshot built; summoned by voice in one live typed-text run (see Voice (Wayback Medium)) |
| Grid Ghost | EskomSePush schedule data, plus a replay mode | Replay tools and a spoken replay alert built against a **synthetic** fixture; live API not implemented |

The shared voice engine (Amazon Nova Sonic on Amazon Bedrock, `voice/`) and the Enron Ghost by voice are built. A live run with typed questions and the real Enron index passed (see Voice below). Microphone input, speaker playback and mascot rendering in a browser have not been tested by a person yet. The Wayback Medium is wired into the voice engine (one live typed run, see below); the Grid Ghost replay alert is wired in the engine and tested against a fake stream (see Grid Ghost replay). See `.kiro/specs/necromancer.md` for the task list.

## Grounding rule

A spirit may only say what a tool returned, and every claim names its source: sender, date and a verbatim quote for emails; archived URL and timestamp for snapshots (spoken as a date the tool derives from the exact 14-digit timestamp, which stays in the evidence panel); stage, area and start time exactly as given for outages. If a tool returns nothing, the spirit says "The trail goes cold." Full rules: `.kiro/steering/grounding.md`.

**When the check runs.** The engine compares what a spirit said with what its tools returned (quoted spans, URLs, timestamps, spoken dates) after the sentence has been spoken. It flags a violation as an error event and in `grounding_violations`; it cannot stop audio that has already played. Prevention rests on the persona prompt and on the tools, which return only archived or indexed text and the exact cold-trail string when they find nothing. A fabricated sentence can therefore be heard before it is flagged. It also cannot check paraphrase, quotes of one or two words, or a spoken number in unusual wording; it flags words about a site's fate or looks (Wayback Medium only) and unverifiable spoken numbers instead.

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
- **Cache:** `data/wayback/` is gitignored and is not shared with a git worktree. Rebuild it with the commands in "Voice (Wayback Medium)" below.
- **Costume:** `mascot/costumes/wayback.json` takes its palette from this snapshot's HTML colours (body `#FFFFCC`, trim `#99CC33`, eyes `#FFCC00`) and records the same URL and timestamp.
- **Reused code:** none.

## Voice (Wayback Medium)
Pre-warm the cache once (about 20 polite requests to the Wayback Machine, at least 1.5 s apart; the voice run itself then needs no network for these pages). The command is safe to repeat: anything already cached costs no request, so a second run makes none.
```bash
.venv/bin/python scripts/prewarm_wayback.py --demo
```
`--demo` caches the capture listing and the readable text of the listed captures for the demo questions: pets.com 1999, 2000 and 2001 and altavista.com 1997. It reads each capture under the URL the listing reports, which is the URL the model is told to pass on. A capture that is a redirect page with no text (three of the 2000 pets.com captures and one 1999 capture) is remembered as empty, so the tools answer "The trail goes cold." for it without a request. Other sites need live requests.
The demo question "Summon pets.com in 1999. What were you selling?" is `list_snapshots("pets.com", 1999)` then `read_snapshot(url, timestamp)`. Both answer from the cache without any network use; "pets.com", "www.pets.com" and `http://www.pets.com/` find the same listing.
The tools say what the archive holds. pets.com 1999 has two HTML captures: 8 May 1999 is mostly menus and links (the tool result says so and the spirit reads out only a readable line), 10 October 1999 is a redirect with no text and is cold. The June 2000 capture has readable headlines.
**Spoken dates.** Tool results carry `spoken_date` ("June 17, 2000") and `spoken_site` ("pets.com"), derived in code from the exact 14-digit timestamp and URL. The spirit says those. The exact timestamp and URL stay in the evidence panel and the tool result; the date is UTC, as the archive stores it, and a timestamp that is not a real calendar date has no spoken date.
Start the server as the Wayback Medium, or start as the Enron Ghost and ask it to summon the Wayback Medium:
```bash
.venv/bin/python -m voice.server --spirit wayback --port 8765
.venv/bin/python -m voice.server --spirit enron --db data/enron/enron.sqlite --port 8765   # then: "summon the Wayback Medium"
```
Live check (one summon and one question, typed, silent microphone; the Wayback fetch function raises, so any network use shows as a failure): `NECRO_LIVE=1 AWS_REGION=us-east-1 .venv/bin/python scripts/live_wayback_e2e.py`. The question is the natural demo one (summon, then "Summon pets.com in 1999. What were you selling?"). It writes a report with a verbatim check of every double-quoted span against the cached snapshot text. Point it at another cache with `NECRO_WAYBACK_CACHE=DIR`.
Limits:
- The spirit speaks only archived text. It makes no claim about layout, images or how the page looked, and there are no screenshots.
- Only the pages named above are cached. Other pages need live Wayback requests, which can be rate limited; on any failure the tools return the cold trail ("The trail goes cold.").
- Snapshot text is capped at 2 MB of HTML before conversion to text.
- The Wayback Medium speaks with the Nova Sonic voice `tiffany`; the Enron Ghost uses `matthew`.
- Live run result (typed input): the summon handed over to the Wayback Medium and the spirit read the cached page with the correct URL, timestamp and a quote that matches the snapshot. It also spoke an unrequested greeting after the summon, and the engine flagged that turn as a grounding violation (false positive: the summon tool result looks like a cold result to the checker). Microphone and speaker use were not tested.
## Grid Ghost replay

The Grid Ghost speaks first, once, when an alert is triggered. The alert text is built in code from the replay fixture (`spirits/grid/spirit.py`, `build_alert`), so the stage, area and start time come from the schedule record, and the spirit adds the sentence "This is a replay of synthetic demo data." The engine summons the Grid Ghost (own stream, voice and amber costume), injects the alert as an engine-written text turn, then hands back to the previous spirit. A spoken alert that differs from the record in stage, area or time is recorded as a grounding violation.

Trigger it with the "Replay grid alert" button, or from a script:

```
curl -X POST -H "Authorization: Bearer $NECRO_ACCESS_CODE" -d '{"area":"Sandton (Demo Area 1)"}' http://127.0.0.1:8765/alert
```

`POST /alert` applies the Origin check and the access code (`Authorization: Bearer` or `X-Necro-Code`) when one is set, and accepts at most 1 KB. Responses: 202 started or visual, 404 no matching outage, 409 an alert is already running. Without a live voice session the alert is shown only (spirit, costume, evidence, a scripted line). It starts no session and uses no cap. `scripts/live_grid_alert.py` is a live check (needs `NECRO_LIVE=1` and AWS credentials; not run).

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
Guardrail settings (flag, or environment variable when the flag is absent): `--max-session-seconds` / `NECRO_MAX_SESSION_SECONDS` (default 180, at most 480), `--idle-seconds` / `NECRO_IDLE_SECONDS` (60), `--daily-cap` / `NECRO_DAILY_CAP` (20), `--total-cap` / `NECRO_TOTAL_CAP` (200), `--usage-log` / `NECRO_USAGE_LOG` (`var/usage.jsonl`), `--caps-file` / `NECRO_CAPS_FILE` (`var/caps.json`), `--demo-video-url` / `NECRO_DEMO_VIDEO_URL`. Set `NECRO_ACCESS_CODE` in the environment to require an access code on `/events` and `/io` (sent as the first WebSocket frame, never in the URL). `--public` refuses to start without that variable. A start that the client cancels (it disconnects) after a cap slot was taken keeps the slot, because a Bedrock stream may already have been opened and billed, and appends a usage line with `end_reason` `start_cancelled` and zero counts so the slot is accounted for. A code that is not valid text (for example a lone surrogate) counts as a failed attempt.
A code that is not valid text (for example a lone surrogate) counts as a failed attempt.
Access code design: when `NECRO_ACCESS_CODE` is set, each WebSocket first receives a `paused` event with reason `code_required`, and the browser must answer within 5 seconds with `{"type":"auth","code":"..."}` as its first frame. A wrong code gets reason `bad_code` and close code 4401; repeated failures get 4429. The server keeps only a SHA-256 digest of the code. The provisional caps (20 per day, 200 in total) are placeholders until T7.5 sets them from a measured session.
The provisional caps (20 per day, 200 in total) are placeholders until T7.5 sets them from a measured session.

### Judge access (T7.10)
Devpost requires working, free access for judges until judging ends (29 Jan 2027, 1:00 pm PT), so judges get a second code that the public caps can never block. Set both codes in the server environment (never in Git, a URL or a log): `NECRO_ACCESS_CODE` for the public and `NECRO_JUDGE_CODE` for judges. The judge code must be at least 12 characters and must differ from the public code, or the server refuses to start and says which rule failed (it never prints a code). The page has one code form that accepts either code and only shows "access granted"; the server decides the role.

| | public code | judge code |
|---|---|---|
| Sessions per day / in total (placeholders) | 20 / 200 | 30 / 150 |
| Session length / idle timeout | 180 s / 60 s | 300 s / 90 s (never above the 480 s stream limit) |
| Counted in | public counters | its own counters in the same `var/caps.json` |

A judge session is a real billed session. It obeys its own length and idle limits and its own daily and total caps, so a leaked judge code can cost at most 150 sessions of 5 minutes. Public caps never block a judge session and judge sessions never use public slots. Only one live session runs at a time: a client that connects while another role's session is live joins it (without a new slot) and can start its own once it ends. When the judge caps are reached the page shows the paused state with the demo-video link. Judge settings: `NECRO_JUDGE_MAX_SESSION_SECONDS`, `NECRO_JUDGE_IDLE_SECONDS`, `NECRO_JUDGE_DAILY_CAP`, `NECRO_JUDGE_TOTAL_CAP` (or the matching `--judge-...` flags). All allowance numbers are placeholders until T7.5 measures a real session and the owner reads the current price in the Bedrock console.

Wrong guesses of either code share one attempt budget per client address (5 per minute) and one global budget (50 per minute), so probing both codes costs the same as probing one. Both codes are compared in constant time against SHA-256 digests. Every usage-log line has `"role": "public"` or `"role": "judge"` (older lines without it are public); the one-line summary adds ` role=judge` for judge sessions. The log holds counts and ids only.

Emergency controls:
- Cut judge or public access on a running server, no restart: create the file `disable-judge` or `disable-public` in the control directory (`NECRO_CONTROL_DIR`, default `var/control`), for example `touch var/control/disable-judge`. It is read on every connection and every second of a live session, so a live session of that role ends within a second. Delete the file to restore access.
- Cut access at startup: set `NECRO_DISABLE_JUDGE=1` or `NECRO_DISABLE_PUBLIC=1` and restart the server.
- Expiry: the judge code stops working after `NECRO_JUDGE_EXPIRES`, an ISO 8601 time with a UTC offset. The default is `2027-01-29T23:59:59-08:00` (end of 29 Jan 2027 Pacific Standard Time, about ten hours after judging ends). Use `none` for no expiry. Changing it needs a restart. To rotate a leaked judge code, set a new `NECRO_JUDGE_CODE` and restart.
- A switched-off, expired or wrong code gets the same `bad_code` answer, so the page never says which.

### What to paste in the Devpost form for judges
Fill the placeholders yourself and send the real code only through the form (the field for judge login credentials), never in the repo:
```
App URL: <https URL of the hosted app>
Access: open the URL, type the judge code below into "Access code", press Unlock, then "Start live voice" and allow the microphone.
Judge code: <JUDGE CODE, set as NECRO_JUDGE_CODE on the server>
Valid until: <date from NECRO_JUDGE_EXPIRES, default 29 Jan 2027 end of day PST>
Limits: each live session lasts up to 5 minutes (90 s of silence ends it); the allowance is <judge daily cap> sessions a day and <judge total cap> in total.
If voice shows "paused": the demo video at <demo video URL> shows the same session; contact <your email> to restore access.
```
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

MIT. See [LICENSE](LICENSE).

Note on the wrong-code lockout: it is keyed on the remote address (5 failures per minute) plus a global limit (50 per minute). Behind a reverse proxy every client shares one remote address, so the global limit is the real guard, and an attacker can lock out legitimate visitors for up to a minute at a time.
