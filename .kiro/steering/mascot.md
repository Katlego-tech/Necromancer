---
inclusion: auto
name: mascot
description: Read when working on the mascot, appearance engine, sprites, costumes, or animation states.
---

# Mascot — 2D Appearance System

## Character identity

The Little Necromancer is a small, charming, slightly mischievous **white floating ghost** with:
- Stitched oversized hood and short ragged cloak
- Cyan luminous eyes
- Simple hands and a circular clasp
- Curled spectral tail

It must read as a ghost, not as a human in a coat.
The silhouette must be recognizable at 64–128 px.

## 2D-first decision (authoritative)

The mascot is **2D sprite-based** for the hackathon MVP (deadline 4 Dec 2026).
3D work is deferred until after submission. Do not generate a 3D model now.
The appearance engine is designed to be renderer-agnostic so 3D can be added later without changing spirit logic.

## Mascot states

| State | Minimum 2D behavior |
|---|---|
| `idle` | Slow CSS vertical float, occasional blink |
| `listening` | Eyes attentive, slight lean (state swap) |
| `speaking` | 2–3 mouth positions, subtle bounce (amplitude signal) |
| `summoning` | Hands lift, short portal/glow overlay |
| `shapeshifting` | Crossfade tint, eyes, prop via palette variables |
| `alert` | Eyes widen/flash, discrete warning ring |

Transitions must never interrupt spoken evidence or obscure the evidence panel.
Always provide a static fallback if assets or animation fail.
Respect `prefers-reduced-motion`. Disable surprise sounds and jump-scares by default.

## Spirit costumes

| Spirit | 2D look additions |
|---|---|
| `enron` | Optional grey lapels/tie, muted green eye accent, paper overlay |
| `wayback` | Swappable browser trim, pixel accents, snapshot-driven palette |
| `grid` | Amber eyes, thin electric ring, warning glyph |

## Appearance configuration schema

All costume changes go through a declarative JSON config object.
Spirit prompts must not hardwire appearance details.

```json
{
  "spirit": "wayback",
  "base": "little_necromancer",
  "renderer": "2d",
  "costume": "retro_web_1999",
  "palette": { "body": "#FFFFFF", "trim": "#8A6ED6", "eyes": "#7DF9FF" },
  "props": ["browser_halo"],
  "animation": "summon",
  "reduced_motion": false
}
```

## Asset naming conventions

```
assets/sprites/idle.png
assets/sprites/blink.png
assets/sprites/talk_open.png
assets/sprites/talk_closed.png
assets/sprites/summon.png
assets/layers/hood.png, body.png, eyes.png, hands.png, clasp.png
assets/effects/
costumes/enron/
costumes/wayback/
costumes/grid/
costumes/events/
```

Master editable source goes in `assets/master/necromancer_base.svg` (or layered PSD/Krita).
Export transparent-background PNG/WebP at consistent canvas size.
Keep full-resolution masters in the repository alongside web-optimized exports.

## Acceptance criteria for 2D MVP

- Single white ghost recognizable and clearly non-human at small UI sizes.
- Idle and speaking states work without distracting from voice playback.
- At least two spirit appearances swap without reloading the voice session.
- One Wayback era theme is traceable to a known cached snapshot or labeled "era-inspired."
- Reduced-motion and disable-surprises controls work.
- Appearance JSON can be consumed by a future 3D renderer without changing spirit logic.
