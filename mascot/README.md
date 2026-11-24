# Little Necromancer: 2D mascot

Plain static HTML/CSS/vanilla JS. No build step, no dependencies. Serve the repo and open `mascot/demo.html`:

    python3 -m http.server 8000   # then http://127.0.0.1:8000/mascot/demo.html

(`file://` is not supported: browsers block `fetch` and CSS masks for local files.)

## Use

    Necromancer.mount(el, {reducedMotion?, spriteUrl?, appearance?}) -> controller
    controller.setState("idle|listening|speaking|summoning|shapeshifting|alert")
    controller.setAppearance(configObject)      // costume swap, no reload
    controller.setSpeakingLevel(0..1)           // drives mouth shape + bounce
    controller.setReducedMotion(true|false|null) // null = follow the OS setting

`summoning` and `shapeshifting` are transient and fall back to the previous resting state.
Costume configs live in `costumes/*.json`, are validated by `appearance.py` against
`appearance.schema.json`, and contain no spirit logic. Spirit prompts never name colours or costumes.
The mascot root clips its content (`overflow: hidden`, `contain: paint`), so no effect can cover the transcript or evidence panel.
No sounds, no jump-scares.

## Real art vs overlay approximations

Only **one** real sprite exists: `assets/sprites/idle.png` (derived from `Cyan-Eyed Ghost Necromancer Mascot Alpha.png`,
which is actually RGB with a flat grey background; the original is untouched). `idle_web.png` is a half-size copy.
`eyes_patch.png` is an alpha mask of the two eye patches derived from idle.png.
Regenerate with `python3 -m mascot.tools.cutout SRC mascot/assets/sprites` and `python3 -m mascot.tools.eyemask mascot/assets/sprites`.

| State / feature | How it is made |
|---|---|
| idle float | CSS transform |
| blink | overlay: eyelid mask (`eyes_patch.png`) clipped closed for 140 ms |
| listening | overlay approximation: lean via CSS rotate. No real "attentive eyes" art |
| speaking mouth | `open` = the sprite's own mouth; `closed`/`mid` = overlay patch + drawn line/ellipse. Bounce from amplitude |
| summoning | overlay approximation: glow sweep + aura + slight rise. Hands do **not** lift |
| shapeshifting | palette CSS variables crossfade (body multiply tint, eye colour blend), brightness pulse |
| alert | overlay: amber eye recolour + flash, thin amber ring. Eyes do **not** widen |
| costume props | SVG overlays (paper, lapels, browser badge, pixel accents, electric ring, warning glyph) |
| static fallback | inline SVG blob ghost if the sprite fails to load |

Real sprite swaps: none yet.

## Known limits

- The eye mask has small stray specks near the top corners of each eye box; they show as faint dots under some palettes.
- Lapels and the paper prop are rough overlays and do not follow the cloak.
- The Wayback palette is labelled `era_inspired`; it is not tied to any cached snapshot.
- Body tint multiplies the whole sprite, so non-white body colours also tint the outline slightly.

## Future artwork needed

1. `blink.png` (closed eyes) to replace the eyelid overlay
2. `talk_open.png` / `talk_closed.png` with proper mouth shapes
3. `summon.png` with raised hands
4. Alert pose with widened eyes
5. Listening pose (tilted head, attentive eyes)
6. Separate layers (`hood`, `body`, `hands`, `clasp`, `eyes`) from a master SVG for proper costume layering
7. Real costume art: lapels/tie that follow the cloak, paper held in the hand
