---
inclusion: auto
name: voice-engine
description: Read when working on the voice engine, Nova Sonic integration, summon logic, or bidirectional streaming.
---

# Shared Voice Engine

## Technology

- **Model:** Amazon Nova 2.5 Sonic via Amazon Bedrock bidirectional streaming.
- **Pattern:** one `BidiAgent`-style session, shared by all three spirits.
- **Reuse:** the voice core pattern is reused from Jozi Buddy. Disclose this in the README.

## Summon pattern

Calling `summon(spirit)` swaps three things atomically:
1. The system/persona prompt.
2. The active tool set (Enron tools, Wayback tools, or Grid tools).
3. Optionally the voice ID, if Nova Sonic supports per-spirit voices.

Nothing else changes. The microphone connection, transcript stream, and evidence panel remain live.

## Latency management

Spirits should say "summoning…" or "searching the archives…" while tool calls are in flight.
Keep spoken replies to one or two sentences; push detail to the evidence panel.
Voice interruption (barge-in) must not corrupt tool call state.

## Transcript and evidence

- The transcript is a live rolling display of agent turns and user turns.
- The evidence panel shows the raw source document (email, snapshot, schedule) cited in the last agent turn.
- These are separate UI components; the voice engine emits events that drive both.

## Tool interface contract

Every spirit tool must return a typed result object with:
- `source`: a citable identifier (email message-id, snapshot timestamp+URL, or schedule entry ID).
- `content`: the raw content to display in the evidence panel.
- `summary`: a one-sentence string the spirit may speak verbatim.

If the tool finds nothing, it returns `{ source: null, content: null, summary: "The trail goes cold." }`.
The agent must pass the `summary` to the voice stream unchanged.

## Grid Ghost proactive alert

The Grid Ghost can inject an alert turn into the bidirectional stream without a user prompt.
Prove this works in replay mode before attempting the live API.
The alert must include stage, area, and start time verbatim from the tool result.
