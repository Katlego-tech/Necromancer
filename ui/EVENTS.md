# Necromancer UI event protocol

The voice backend sends one JSON object per WebSocket text frame. The UI connects with
`index.html?ws=ws://host:port/path` and reconnects with exponential backoff (0.5 s up to 10 s).
Without `ws`, `index.html?mock=enron` replays a synthetic scripted session (`&speed=2` for faster).

Every event has a string `type`. Unknown extra fields are ignored. A frame that is not valid JSON,
has an unknown `type`, or fails the field rules below is ignored and listed under "Protocol issues"
on the page. It never stops later events from being processed.

All text is rendered as plain text. Email bodies and archived HTML are untrusted and are never parsed as HTML.

## Events

### transcript
Required: `turn_id` (non-empty string), `role` (`user` | `spirit`), `text` (string), `final` (boolean).
Frames with the same `turn_id` replace that turn's text, so partial (interim) text is sent as
`final: false` and the last frame for the turn has `final: true`.

```json
{"type":"transcript","turn_id":"u1","role":"user","text":"Who moved","final":false}
```
```json
{"type":"transcript","turn_id":"u1","role":"user","text":"Who moved the review?","final":true}
```

### state
Required: `state` (`idle` | `listening` | `speaking` | `summoning` | `shapeshifting` | `alert`).
Forwarded to the mascot's `setState`.

```json
{"type":"state","state":"speaking"}
```

### spirit
Required: `spirit` (`enron` | `wayback` | `grid`). Shows which spirit is active.

```json
{"type":"spirit","spirit":"wayback"}
```

### appearance
Required: `config` (object). The declarative appearance config from `.kiro/steering/mascot.md`
(`spirit`, `base`, `renderer`, `costume`, `palette`, `props`, `animation`, `reduced_motion`).
The UI does not interpret it; it is forwarded to the mascot's `setAppearance`.

```json
{"type":"appearance","config":{"spirit":"wayback","base":"little_necromancer","renderer":"2d","costume":"retro_web_1999","palette":{"body":"#FFFFFF","trim":"#8A6ED6","eyes":"#7DF9FF"},"props":["browser_halo"],"animation":"summon","reduced_motion":false}}
```

### evidence
Replaces the contents of the evidence panel. The panel stays visible while the spirit speaks.

Required: `kind` (`email` | `snapshot` | `grid`), `source` (citable id string, or `null` for a cold trail),
`content` (raw source text string, or `null` exactly when `source` is `null`), `metadata` (object whose
values are scalars or arrays of scalars; optional only when `source` is `null`).
Optional: `excerpt` (string). If it occurs verbatim in `content`, the first occurrence is wrapped in a
`<mark>` element. The displayed text is never altered. If it does not occur, nothing is marked.

Metadata keys required when `source` is not null (all shown in the order given, values verbatim):

| kind | required metadata keys |
|---|---|
| email | `evidence_id`, `sender`, `recipients`, `date`, `subject`, `source_path` |
| snapshot | `url`, `timestamp` (exactly 14 digits, e.g. `19990101000000`) |
| grid | `stage`, `area`, `start` (exactly as the schedule returned them) |

```json
{"type":"evidence","kind":"email","source":"ev-0001","content":"Line one.\nLine two.","excerpt":"Line two.","metadata":{"evidence_id":"ev-0001","sender":"a@example.invalid","recipients":["b@example.invalid"],"date":"2001-01-01T00:00:00Z","subject":"Hi","source_path":"x/y/1."}}
```
```json
{"type":"evidence","kind":"snapshot","source":"19990101000000 http://example.invalid/","content":"plain text of the archived page","metadata":{"url":"http://example.invalid/","timestamp":"19990101000000"}}
```
```json
{"type":"evidence","kind":"grid","source":"grid-1","content":"Stage 2, Area X, 18:00","metadata":{"stage":2,"area":"Area X","start":"18:00"}}
```
Cold trail (the panel shows "The trail goes cold." and nothing else):
```json
{"type":"evidence","kind":"email","source":null,"content":null}
```

### error
Required: `message` (string). Listed under "Protocol issues".

```json
{"type":"error","message":"tool timed out"}
```

## Invalid examples (must be rejected)

```json-invalid
{"type":"transcript","role":"user","text":"no turn id","final":true}
```
```json-invalid
{"type":"state","state":"dancing"}
```
```json-invalid
{"type":"evidence","kind":"snapshot","source":"s","content":"x","metadata":{"url":"u","timestamp":"1999"}}
```
```json-invalid
{"type":"evidence","kind":"email","source":null,"content":"invented text"}
```
```json-invalid
{"type":"nope"}
```

## Mascot interface (Track C, `mascot/mascot.js`)

`mount(element)`, `setState(state)`, `setAppearance(config)`. See `ui/mascot-slot.js`.
