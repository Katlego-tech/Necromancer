# Necromancer

One voice, three spirits. A real-time voice agent that "speaks with the dead": a bankrupt company's email archive, a vanished internet, and a dying power grid.

Built for **Kiroween 2026**, Frankenstein category, with Kiro (specs, steering files, hooks). The `.kiro/` directory at the repo root holds the specs, steering and hooks used to build it.

## The three spirits

| Spirit | Stitched to | Status |
|---|---|---|
| Enron Ghost | CMU Enron email dataset, indexed in SQLite FTS5 | Search tools and ingestion pipeline built and tested |
| Wayback Medium | Wayback Machine CDX and raw `id_` snapshot APIs | Tools with offline cache built and tested; no site cached yet |
| Grid Ghost | EskomSePush schedule data, plus a replay mode | Replay tools built against a **synthetic** fixture; live API not implemented |

The shared voice engine (Amazon Nova Sonic on Amazon Bedrock) is **not built yet**. See `.kiro/specs/necromancer.md` for the task list.

## Grounding rule

A spirit may only say what a tool returned, and every claim names its source: sender, date and a verbatim quote for emails; archived URL and 14-digit timestamp for snapshots; stage, area and start time exactly as given for outages. If a tool returns nothing, the spirit says "The trail goes cold." Full rules: `.kiro/steering/grounding.md`.

**Replay data is synthetic.** `data/fixtures/grid_replay.json` contains invented outage events for demonstration. It is labeled as such in the file and is not real Eskom or EskomSePush data. Any demo that uses it must say it is a replay.

## Mascot

A 2D white ghost, "The Little Necromancer". The mascot is 2D for the hackathon; 3D is deferred until after submission. Appearance is a declarative JSON config, kept separate from spirit logic and evidence.

## Code provenance and disclosure

- **Written for this project during the submission period:** everything in this repository, unless listed below.
- **Reused from another project:** the Nova Sonic bidirectional voice session is intended to be adapted from the author's earlier project, *Jozi Buddy*. **TO CONFIRM:** nothing from Jozi Buddy has been copied in yet. When it is, list the exact files and what was changed here.
- **Third-party data:** the [CMU Enron Email Dataset](https://www.cs.cmu.edu/~enron/) (May 7, 2015 version). It is not included in this repository. See the privacy note below.
- **Mascot artwork:** the reference images in the repo root. **TO CONFIRM:** record how they were made (tool, author, terms of use) before submission.

## Privacy note on the Enron data

The dataset was made public during a federal investigation and contains real people's email. Many of the people in it were not involved in any wrongdoing. The CMU distributor asks users to be sensitive to their privacy. This project quotes emails only as historical evidence, and the Enron Ghost should focus on the executives and events in the public record.

## Setup

Requirements: Python 3.10+ with SQLite FTS5 (standard library only; no pip packages), Node for the UI tests (developed with v24).

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
