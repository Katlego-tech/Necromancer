# Necromancer: one voice, three spirits

> A voice agent that speaks with the dead: a bankrupt company, a vanished internet and a dying power grid.

Kiroween 2026 entry, **Frankenstein** category ("stitch incompatible technologies together").
Each spirit stitches a 2026 real-time voice model to a different piece of dead or dying tech, so the
demo shows three Frankenstein stitches in one project.

*Idea saved 17 Nov 2026.*

---

## Hackathon facts

| | |
|---|---|
| Category | Frankenstein: $5,000 category prize (overall prizes $30k / $20k / $10k) |
| Bonus prizes that stack | Best Demo ($1,000), Best Student Project ($1,500 per category, if you're a student), Best Startup ($10,000, founders only) |
| **Kiro credit request deadline** | **1 Dec 2026** |
| **Submission deadline** | **4 Dec 2026, 2:00 PM PT (midnight in Johannesburg)**. No extensions |
| Judging | 16 Dec 2026 – 29 Jan 2027. Equally weighted criteria, including potential impact and presentation |

**Submission requirements**
- A new app built with Kiro during the submission period (which opened 30 Oct 2026). Disclose any existing code you reuse.
- A public, open-source GitHub repo with **`/.kiro` committed at the root**.
- A demo video under 3 minutes on YouTube.
- App URL, description, a write-up of how you used Kiro, and the category.

**Still to check:** whether South Africa is an eligible country. See the official rules on Devpost.

---

## The three spirits

| Spirit | Stitched to | Example question | Priority |
|---|---|---|---|
| **The Enron Ghost** | The CMU Enron email dataset (about 500k emails in maildir format), indexed in SQLite FTS5 | *"What did Ken Lay know about the losses, and when?"* It answers with sender, date and a quote from the actual email | **Must-have.** Build first |
| **The Wayback Medium** | Wayback Machine APIs (CDX for the snapshot list, raw `id_` snapshots for page content) | *"Summon pets.com in 1999. What were you selling?"* It answers in character from archived pages, with the snapshot date | **Should-have** |
| **The Grid Ghost** | The EskomSePush API (load-shedding schedules and status) | It **speaks first** to warn you: *"Stage 4 reaches your area in 20 minutes. Charge your phone and boil the kettle now."* Alerts only, with no device control | **Could-have.** Cut first if time runs short |

**The Grid Ghost needs a replay mode.** Load-shedding has been rare lately, and the free EskomSePush
tier limits how many calls you can make per day. A "replay a past outage" mode keeps the demo
working whatever Eskom does on recording day.

---

## Architecture

```
Mic ⇄ Nova 2.5 Sonic (Strands BidiAgent, reused from Jozi Buddy and disclosed)
        │
        ├─ summon(spirit) → swaps the persona prompt, the tool set and, if possible, the voice
        │
        ├─ Enron tools:   search_emails · read_email · who_wrote_to(person, dates)
        ├─ Wayback tools: list_snapshots(url, year) · read_snapshot(url, timestamp)
        └─ Grid tools:    next_outage(area) · current_stage()  [alerts only]
        │
   Screen: live transcript + "evidence panel" (the email or snapshot being quoted)
```

**Grounding rule (all spirits):** a spirit may only say what a tool returned, and every claim names
its source email or snapshot. If nothing comes back, the spirit says the trail goes cold. This
goes in a Kiro steering file, and it's what makes the séance credible rather than made up.

**Alert rule:** the Grid Ghost only warns. It states the stage, the area and the start time exactly as
the schedule gives them, and never guesses a time.

---

## The 3-minute demo

1. **0:00** Dark screen. *"I speak with the dead. Who shall we summon?"*
2. **0:15 Enron.** "What did executives say about Raptor?" The ghost answers, and the real email
   slides into the evidence panel. A follow-up question digs deeper.
3. **1:15 Wayback.** "Summon a website that died in 2001." The medium speaks as the dead site, with the
   archived page shown behind it.
4. **1:55 Grid.** Mid-conversation, the Grid Ghost interrupts on its own to warn that load-shedding
   is coming to your area, with the stage and start time shown on screen.
5. **2:30** The stitch diagram and the Kiro story (specs, steering, hooks).

---

## 17-day plan, with cut lines

| Days | Dates | Work | If you're behind |
|---|---|---|---|
| 1 | 18 Nov | New repo, Kiro spec (requirements, design, tasks), steering files, **request Kiro credits** | — |
| 2–3 | 19–20 Nov | Download the Enron data, build the FTS5 index, search tools + tests | — |
| 4–6 | 21–23 Nov | Reuse the voice core, add `summon` and the Enron ghost end to end | **Never cut this** |
| 7–8 | 24–25 Nov | Evidence panel (simple web view of transcript and source) | Fall back to a plain terminal transcript |
| 9–11 | 26–28 Nov | Wayback Medium | Cut here and you still have a solid entry |
| 12–13 | 29–30 Nov | Grid Ghost: replay mode first, live API second | **Cut first** |
| 14–15 | 1–2 Dec | Record and edit the video | — |
| 16–17 | 3–4 Dec | Buffer, README, check `/.kiro` is committed, submit before **4 Dec 2 PM PT** | — |

---

## Kiro story for the write-up

- **Specs:** one spec per spirit, all built on the shared core.
- **Steering:** the grounding rule, the persona rules, and the alert rule.
- **Hooks:** run the tests whenever a file is saved, and check that no spirit's reply leaves out its source.
- **Repo:** make it a fresh repo and commit `.kiro`. The Cultivation kit used in Jozi Buddy hides
  `.kiro/steering/kiro.md` from git, and the rules require `/.kiro` at the repo root.

---

## Risks

- **Search quality over 500k emails.** Start with FTS5 keyword search. Add embeddings (e.g. Bedrock Titan) only if answers miss.
- **Tool-call delays in voice.** Have the spirit say "summoning…" while it works, and keep spoken replies to one or two sentences.
- **Wayback Machine rate limits.** Cache snapshots locally and pre-warm the ones used in the demo.
- **The Grid Ghost speaking first.** This needs the agent to start a turn on its own (inject an event into the bidirectional stream). Prove it works early in days 12–13, or script it for replay mode.
- **Scope.** One polished spirit beats three shaky ones. Stick to the cut order.
