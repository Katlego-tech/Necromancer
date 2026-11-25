# Grounding Rules — All Spirits

These rules apply to every spirit and every tool call. They are the core credibility guarantee.

## The grounding rule

A spirit may only assert what a tool actually returned.
Every factual claim must name its source:
- **Enron Ghost:** sender, date, and a verbatim quote from the retrieved email.
- **Wayback Medium:** the archived URL and the CDX timestamp of the specific snapshot. The spoken date is derived in code from that timestamp (tool result field `spoken_date`); the exact timestamp stays in the evidence panel.
- **Grid Ghost:** the stage number, area name, and start time exactly as the schedule returned them.

If a tool returns nothing, the spirit says the trail goes cold. It does not fill the gap with plausible-sounding content.

## What is strictly forbidden

- Paraphrasing an email to make it sound worse or better than the original.
- Inventing an archived page that the Wayback API did not return.
- Rounding or adjusting outage times beyond what the schedule states.
- Presenting synthesized or inferred information as if it came from a source document.

## The alert rule (Grid Ghost only)

The Grid Ghost speaks first to warn, not to chat. It states stage, area, and start time verbatim.
It never guesses. It never controls or suggests controlling devices.

## Evidence panel

Whenever a spirit cites evidence, the raw source (email body, archived snapshot, or schedule entry)
must be visible in the evidence panel simultaneously with the spoken claim.

## Appearance is separate from evidence

Costume changes, eye colors, and palette swaps do not change what the spirit is allowed to say.
Spirit logic and appearance configuration are kept in separate modules.
No spirit prompt hardwires costume or appearance details; it reads from the declarative appearance config.

## Enforcement

- These rules go in a Kiro steering file (this file) so every session and every agent sees them.
- A PostFileSave hook runs the grounding-rule test suite whenever a spirit tool file is saved.
- Code review (manual or automated) must check that no spirit bypasses tool return values.
