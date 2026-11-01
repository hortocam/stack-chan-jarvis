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
