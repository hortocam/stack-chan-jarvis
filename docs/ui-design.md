# Jarvis UI on the StackChan screen — what is possible, and what it looks like

**Date:** 2026-11-01 · **Owner brief:** green-ish palette, idle globe + clocks + log, conversation
"cat's-eye marble" that emotes. **Status:** concept approved to design, not yet implemented.
**Companion images:** `docs/ui/mock-idle.png`, `docs/ui/mock-conversation.png` (320×240 device
frames, rendered at 2×).

## The constraint that shapes everything

The panel is **320 × 240 px, 2.0-inch, SPI (ILI9342C)** with 8 MB PSRAM behind it. That is
153,600 bytes per 16-bit frame. Every design decision below follows from that:

| Budget | Value | Consequence |
|---|---|---|
| Full-frame blit | ~150 KB | possible, but not every frame — use **partial refresh** |
| Practical animation | 15–20 fps in a 150×150 region | the marble and globe live in a ~150 px circle, not full-screen |
| Text | 7.5–9 px monospace | the log column is ~24 characters wide, 5 lines |
| Palette | 5 greens + one amber | dithering, not gradients, at this colour depth |

## Layout rules (owner feedback, 2026-11-01 — these are constraints, not preferences)

1. **The circle never moves and never resizes.** Fixed at `cx=100, cy=120, r=56` in *every*
   state, idle and conversation alike. Nothing — headline, transcript, alert — is allowed to
   push, shrink or recentre it. If something must appear, it appears *over* or *beside*.
2. **Event blurbs are an overlay in the right column** (`x ≥ 178`), using the real estate that
   is empty in idle. The globe keeps **latitude centred and rotates only in longitude**, so
   the right-hand column is always free and a ping never needs to move anything.
3. **Motion vocabulary — direction carries meaning:**

   | State | Motion |
   |---|---|
   | Listening | rings travel **inward**, rim → core: the circle drawing sound in |
   | Thinking | **flat** spinning arcs, no perspective, counter-rotating |
   | Speaking | wavefronts radiate **outward** from the rim and **fade with distance** |
   | Attention | heavy, slow, amber; nothing radiates |

4. **The marble is a lava lamp**, not a crystal ball: two or three large slow blobs drifting
   behind a soft slit — not glitter, not a starfield, not a static gradient.

## Layout (shared by both modes)

```
┌──────────────────────────────────────────────┐
│ J A R V I S     UTC 17:56  LOCAL 12:56  EST  │  19 px header, thin rule
├──────────────────────────────────────────────┤
│                                              │
│              ╭────────────╮                  │
│              │  THE CIRCLE │  ← hero element  │
│              ╰────────────╯                  │
│                                              │
│ 12:54 media ▸ 3 imports queued      IDLE     │  5 log lines, 7.5 px
└──────────────────────────────────────────────┘
```

The **circle is the constant**: it is a globe when idle, a marble when talking. Same radius,
same centre, same rim — so a conversation reads as the same object changing state, not a new
screen.

## Idle mode — the globe

- **Centred on the viewer's location** (Boston), orthographic projection.
- **Land is a dot matrix**, not a texture: a 64×36 mask rasterised at build time from Natural
  Earth (public domain) into a C array. ~2,000 dots, projected per frame — cheaper than
  pre-rendered frames and it rotates smoothly.
- **Day/night** is a computed terminator from the device clock + lat/long (no network): the
  night side is veiled, the day limb is brighter. This is real, not simulated — it will match
  the actual sun.
- **Pings**: the gateway pushes `globe_focus(lat, lon, label, headline)`. The globe eases to that
  location over ~600 ms, the marker pulses three times, a headline card holds for 8 s, then it
  returns home and pings there. Simulated events are just a canned list until the feed exists.
- **Log lines** are pushed as one-line events (media imports, lab alerts, cron digests, CI
  results). Newest at the top, oldest fades. This is the cheapest "driven by the real lab" win
  we have — the data already exists on 914.

## Conversation mode — the marble

Same circle, no land. A living core with a horizontal **cat's-eye slit** that behaves like an
iris. States are deliberately legible at 150 px:

| State | Slit | Plasma | Rim | Trigger |
|---|---|---|---|---|
| **Listening** | narrow filament, wide | calm, slow drift | 3 breathing rings | wake word / VAD open |
| **Thinking** | narrow, drifts up-left | sinks and swirls | 3 dashed orbit arcs | transcript sent, no first token yet |
| **Speaking** | opens on vowels | pushed outward per syllable | jagged envelope | TTS audio playing |
| **Attention** | wide, amber core | slow, heavy | amber rim | lab alert, error, "this needs you" |
| **Sleeping** | closed | near-still | dim, occasional pulse | 2 min idle |

**Reactivity is real, not decorative:** the rim follows the actual audio envelope from the TTS
stream we already receive, so lip-sync and "emoting" come from the same buffer that drives the
speaker.

## How it gets drawn on lane A (ESP-IDF + LVGL)

1. **New app in the mooncake framework** (`firmware/main/apps/`), our own source in our own repo,
   added as a component — not a patch to M5's files. It owns the screen in AI mode.
2. **Globe**: dot projection per frame into an LVGL canvas (`lv_canvas`), partial-invalidate the
   150 px region only. Est. 3–5 ms/frame on the S3 — comfortable at 15 fps.
3. **Marble**: a 96×96 plasma field computed per frame (2-octave value noise + radial falloff),
   blitted to a canvas and scaled; slit and rim drawn as LVGL primitives on top. Est. 6–9 ms/frame.
   If that misses the frame budget, fall back to **pre-rendered sprite sheets** (8 states × 24
   frames at 4 bpp ≈ 1.4 MB in the `xs`/assets partition) — a straight swap behind the same
   interface.
4. **Data in**: a tiny UI tool surface on the gateway (`self.ui.*`, alongside the six body tools),
   delivered over the same WebSocket as MCP calls. No second socket, no polling, no new service.

## What is honestly hard, and what is off the table

- **Hard:** the marble's *organic* motion at 15 fps. The concept images use SVG turbulence; the
  device gets maths. It will look like a lava lamp, not a fluid sim — accepted.
- **Off the table:** textured 3D globe, video, true alpha blending, more than ~6 colours,
  full-screen 30 fps, anything resembling a blur.
- **Untested:** all frame timings above are estimates until the hardware lands on 5 Nov. The
  first card after unboxing should measure them before the design is frozen.

## Phases

| Phase | Deliverable | Needs |
|---|---|---|
| **UI-0** | Concept frames (this document) | done |
| **UI-1** | Idle: dot globe, terminator, clocks, log feed | device; `self.ui.*` gateway tools |
| **UI-2** | Conversation: marble states + audio-reactive rim | device; emotion events we already send |
| **UI-3** | Event pings: focus/headline/return-home | gateway feed for news + lab events |
| **UI-4** | Transitions, sleeping state, skin polish | device |

## Wire contract — `self.ui.*` over the existing MCP session

**Status:** implemented in the gateway (card `t_b4d67050`); the firmware app builds to this
section. No second socket, no polling: each UI call is one JSON-RPC `tools/call` inside the
`type: "mcp"` frame the voice socket already carries.

### Envelope (gateway → device)

```json
{
  "session_id": "<the id from the server hello>",
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "id": 7,
    "method": "tools/call",
    "params": { "name": "self.ui.log_line", "arguments": { "text": "git: PR #11 merged" } }
  }
}
```

The device answers with the same `id` in an `mcp` payload, exactly as `mcp_server.cc` does for the
body tools: `result.content[]` + `result.isError`, or `error{code,message}`. The four tools must
appear in `tools/list`; the firmware's `tools/call` dispatch accepts them without requiring an
`initialize` first (v2.2.4 does not gate on it).

### Tools

| Tool | Arguments | Limits / defaults |
|---|---|---|
| `self.ui.globe_focus` | `lat` number, `lon` number, `label` string, `headline` string, `hold_ms` int | lat −90…90, lon −180…180, label 1…24 chars, headline 1…72 chars, hold_ms 1000…60000, default **8000**. Ease (~600 ms), pulse, hold, return home. |
| `self.ui.log_line` | `text` string | 1…120 chars; one line in the log column (newest first); the device may wrap or elide. |
| `self.ui.state` | `name` string | one of `idle`, `listening`, `thinking`, `speaking`, `attention`, `sleeping`. |
| `self.ui.clocks` | `utc` string, `local` string, `tz` string | `utc`/`local` are `HH:MM` 24-hour; `tz` is a label of 1…8 characters (`EST`). Pushed on the minute. |

Every argument is validated in the gateway. An unknown tool name, a missing field, an extra field
or an out-of-range value is refused before a frame is sent and logged at debug; it never reaches
the device. This is deliberate: the device is a display, not a validator.

### Delivery rules

- **Best-effort.** A call is dispatched in the background; the voice turn never awaits it. The
  deadline is 1.0 s. A timeout is one debug line and the call is dropped; a device `error` or
  `isError: true` is one warning line and the call is dropped. No retry.
- **One queue, bounded.** The bus holds at most 64 events. When full it drops the oldest, not the
  newest.
- **Coalescing.** `state`, `clocks` and `globe_focus` replace their own pending value (latest
  wins). An identical `log_line` replaces itself.
- **Rate cap.** At most 4 UI calls per rolling second are dispatched; excess is dropped with a
  counter. A webhook storm cannot flood the screen.
- **Honest when alone.** With no device attached, `publish` drops immediately with a debug line.
  Nothing is buffered, so a reconnect never receives a backlog.

### Sources

`jarvis_gateway/ui_sources.py` maps the payloads the lab already produces to events, in this
order: git/PR (Gitea or GitHub), kanban card transitions, cron digests, Uptime-Kuma alerts.
`feed.py` is the headline source (news + markets); it starts as a **canned** list behind
`JG_UI_FEED_ENABLED=false` and is not a live feed yet. The gateway adds no webhook listener — the
existing observer hands the decoded payload to `UiEventBus.publish_source`.

Blocked/failed kanban cards and DOWN alerts also raise `state: attention`; everything else is a
log line. There is no automatic `listening` / `thinking` / `speaking` frame: the firmware already
sees `stt`, `llm` and `tts` on the same socket and derives those states from them (UI-2), so the
voice path stays free of extra writes. `self.ui.state` is for idle/attention/sleeping overrides.

### Counters

`UiEventBus.stats` exposes `dispatched`, `coalesced`, `dropped_no_device`, `dropped_queue_full`,
`dropped_rate`, `pending`, `in_flight`; `UiChannel.stats` exposes `sent`, `ok`, `errors`,
`timeouts`, `dropped`. These are the evidence for the "flooded queue is capped" test.

