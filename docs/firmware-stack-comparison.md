# Firmware stack comparison — lane A (`m5stack/StackChan`, ESP-IDF) vs lane B (`stack-chan/stack-chan`, Moddable)

**Task:** t_46cd3edd · **Date:** 2026-11-01 · **Author:** research profile
**Workspace:** `/home/hermes/projects/jarvis-hardware`
**Question (Cameron):** the community is more geared to `stack-chan/stack-chan`, and that repo
ships a browser simulator. Is the community stack the better development platform for Jarvis?

**Out of scope, and honoured:** nothing flashed, nothing purchased, neither upstream edited, no
gateway change. Everything below is read from source; the only writes are this file and a
scratch clone.

## Verdict, in one table

| # | Axis | Lane A — `m5stack/StackChan` (ESP-IDF, C++, LVGL) | Lane B — `stack-chan/stack-chan` (Moddable XS/JS, Piu) | Winner for Jarvis v1 |
|---|---|---|---|---|
| 1 | **Wake word ("Jarvis")** | On-device Espressif `esp-sr` WakeNet9, `CONFIG_SR_WN_WN9_*`, AFE path, wake in listening *and* speaking (barge-in aborts TTS), sends `listen{state:detect,text}` + wake audio | **None.** No detector anywhere in the tree; `notifyWakeWordDetected()` exists but nothing calls it. Roadmap parks "wake word architecture decision" in **Phase 5 (Apr 2027+)**. Turn starts from a Drawer (touch) toggle or a physical button | **A, decisively** |
| 2 | **Protocol fit to v2.2.4** | *Is* xiaozhi-esp32 v2.2.4 + M5 patch, pinned in `repos.json`. OTA discovery, hello, listen modes, `detect`, `abort(wake_word_detected)`, 60 ms Opus, MCP JSON-RPC — all native | A clean, strictly-validated **v1 client** (`xiaozhi-contract.ts` + `xiaozhi-model.js`) that speaks the same wire format — but with **no OTA**, an **auto-listen-on-connect** model with **no client VAD**, and **no barge-in**. Reachable only through `ChatService`'s `connection` option, which **no shipped example or mod uses** | **A** on completeness; **B** is wire-compatible enough to be worth a spike, not to lead |
| 3 | **Body control (our six tools)** | Already six MCP tools in firmware: `self.robot.get_head_angles`, `set_head_angles`, `set_led_color`, `create_reminder`, `get_reminders`, `stop_reminder` | Motion (`lookAt`/`setPose`, radians) and LED (`on/off/blink/rainbow`, 0–255) exist as JS capabilities; **reminders do not exist at all**; MCP tools are whatever JS the mod registers | **A** |
| 4 | **Simulator coverage** | No browser story. QEMU/Wokwi were covered in `notes/simulator-spike.md`: boot-only / display-only | Real and good — the WASM host boots the actual app, renders the 3D model, animates the face, serves touch/buttons/servo rotation, camera via `getUserMedia`, buffered mic/speaker — **and mods load into it**. But **no Wi-Fi, no WebSocket, no Opus, no conversation module**: it cannot reach our gateway | **B**, for UI/mod iteration only |
| 5 | **Iteration speed** | Any face/behaviour/tool change = edit C++/LVGL → `idf.py` build → full flash. Mod-loader: none | MOD = JS/TS archive (`.xsa`), built in-browser or `npm run mod`, written to the `xs` partition over USB serial; **host firmware untouched**. Block editor (Blockly) and face DSL/editor generate the same archives. Simulator runs it before hardware | **B, decisively** |
| 6 | **Maintenance risk** | Vendor-owned (M5Stack), MIT in `firmware/`, 1,476★/412 forks, pushed 2026-10-29, no releases, no root LICENSE, depends on a forked xiaozhi + 4 third-party components + our `sdkconfig` overlay | Community, Apache-2.0, 1,839★/244 forks, 65 open issues, but **one maintainer owns ~1,661 of ~2,050 commits**, the whole thing was **re-architected 12 days ago (v2.0.0/v2.0.1, 2026-10-20)**, docs are Japanese-first, and its own soak test measured a **17.98 s mic stall** | **A** for a v1 we must not babysit; **B** is fine as a second target we keep at arm's length |

**Recommendation, plainly: stay on lane A. Do not switch.** Build the Jarvis v1 bring-up on
`m5stack/StackChan` + our gateway exactly as `docs/stackchan-jarvis-plan.md` says. Take lane B's
*ideas and tooling* — the MOD/XSA split, the browser simulator as a face/behaviour preview, the
half-duplex + server-VAD turn discipline, the web installer pattern — not its runtime. If we want
feature velocity later, the cheapest lane-B experiment is **one MOD** (a few hundred lines of JS)
that points the already-shipped `xiaozhiV1` worker at our gateway; that is a spike, not a platform
decision.

---

## What I actually read, and how

| Source | What | Notes |
|---|---|---|
| `stack-chan/stack-chan` @ `e7e27e2f8b89` (2026-11-01, shallow clone into scratch) | whole `firmware/host`, `firmware/mods/examples`, `firmware/host/platforms/{wasm,m5stackchan_cores3}`, `web/{simulator,src,editor,avatar-dsl,face-editor}`, `docs/{ROADMAP,specs,operations,investigations}` | read, not executed (no node_modules; see "Reproducing") |
| `m5stack/StackChan` @ local clone (`.research_stackchan/StackChan`, `1b576559`, 2026-08-19) + GitHub API | `firmware/repos.json`, `main/hal/hal_mcp.cpp`, `main/Kconfig.projbuild`, `sdkconfig.defaults`, `firmware/LICENSE` | read |
| `78/xiaozhi-esp32` @ **v2.2.4** (`e77dedb`, local clone) | `docs/websocket.md` (full), `docs/mcp-protocol.md`, `main/protocols/protocol.cc`, `main/protocols/websocket_protocol.cc`, `main/application.cc` | this is the pinned spec *and* the lane-A implementation |
| GitHub REST API | stars/forks/issues/contributors/releases for both repos, 2026-11-01 | numbers below |

Repo health, 2026-11-01:

```
stack-chan/stack-chan : 1839★ 244 forks 65 open issues Apache-2.0, created 2021-06-06, pushed 2026-11-01
                        releases v2.0.0 + v2.0.1 (both 2026-10-20), before that v1.1.0 2026-08-25
                        contributors: ~26; meganetaaan 1661 commits, next 235, then bots
m5stack/StackChan     : 1476★ 412 forks 33 open issues, license detected: none at root (firmware/LICENSE is MIT),
                        created 2026-01-07, pushed 2026-10-29, no releases
                        contributors: ~12
```

---

## 1. Does the simulator run the conversation/voice path? **No. It is UI + MODs only.**

The simulator is not a mock: `web/simulator/` boots the **real host application compiled to
WebAssembly** (`docs/specs/visual-programming.md`: "the Web simulator places the saved XSA into
WASM memory and then starts the firmware", waits for the `[main] app behaviors ready` trace), and
`web/simulator/bridge.mjs` + `web/src/services/simulator/simulator-engine.mjs` provide the host
side: `Display` (three.js 3D model + 320×240 canvas), `Touch`, `Host.Button` (A/B/C), servo
`applyRotation`/`setTorque`, `Camera` (`getUserMedia`), and `AudioOut`/`AudioIn`.

What it cannot do, with the specific reasons:

1. **No conversation module in the WASM build.** `firmware/host/platforms/wasm/manifest.json`
   includes `ui`, `audio`, `motion`, `camera`, `connectivity`, `input`, `util`, `preferences` —
   **`conversation` is not in the list**. No `chat`, no `xiaozhiV1Model`, no
   `stackchanServerChatWebSocketWorker`, no `stackchanOpusEncoder/Decoder`.
2. **The xiaozhi client is gated to one native target.** `firmware/host/modules/conversation/manifest.json`
   sets `"xiaozhiV1Available": true` **only** under `esp32/m5stackchan_cores3`, and
   `firmware/host/modules/testing/module-structure.architecture.ts` has a test — *"XiaoZhi is
   enabled only for targets with its complete module graph"* — asserting that any target with
   `xiaozhiV1Available` also carries the Opus encoder/decoder. Those Opus modules exist only under
   `firmware/host/modules/audio/platforms/m5stackchan-cores3/`. WASM, `mac`, plain `m5stack_cores3`
   and `stackchan_rt` all get the plain `worker-stack` (OpenAI/ElevenLabs/etc.) or nothing.
3. **No network in the simulator at all.** `firmware/host/modules/connectivity/wasm/network-manager.ts`
   is a stub whose only real statement is `trace('[network] Wi-Fi is unsupported on wasm: ' + ssid)`
   and `onError('unsupported')`. The WebSocket transport needs `device.network.ws`/`wss`
   (`server-chat-websocket-worker.js`), which the wasm platform does not provide.
4. **Audio is buffered, not streamed.** `firmware/host/modules/audio/wasm/audio-bridge-contract.ts`
   exposes exactly `startRecord(durationMs)` → `MediaRecorder` blob and `startPlayBuffer()` →
   `AudioContext.decodeAudioData` (`web/simulator/bridge.mjs`, `createHostAudioInBridge` /
   `createHostAudioOutBridge`). The xiaozhi path needs the opposite: a **continuous native PCM ring**
   (`worker-stack.js`: `SharedArrayBuffer` ring, `writePcmRing`, 1920-byte frames) that the Opus
   task drains every 20 ms. There is no such ring in WASM.

So the honest answer to "can it drive our gateway end to end (OTA → ws → audio) from a browser":
**no, and none of the three legs exists** — the client has **no OTA step at all** (it connects to a
configured `ws://host:port/path`, see §3), the wasm platform has **no ws**, and it has **no Opus**.
Our Python synthetic device (`sim/synthetic-device/`) remains the only end-to-end wire test.

What the simulator *is* genuinely good for — and this is real value:

- seeing the **face** animate, the Jarvis skin read, the drawer, speech-balloon layout, emotion
  transitions, touch-pet behaviour, camera preview, servo rotation, **without hardware**;
- running a **MOD** (including a block-editor-generated one) end to end minus its network legs;
- a **fast failure surface**: the editor refuses to deploy if firmware doesn't reach
  `app behaviors ready` in 30 s, or if the archive's XS version doesn't match the target profile.

That is exactly the "desktop face" job `notes/simulator-spike.md` priced at 1–2 days on lane A
(compiling `avatar/skins/default` against `smooth_ui_toolkit` + SDL2). Lane B gives it for free —
**but for lane B's face stack (Piu/JS), not for lane A's LVGL skin**.

---

## 2. Wake word: there is none. What a turn actually costs, and what "Hey Jarvis" would cost.

**No on-device wake-word support exists in lane B.** Evidence:

- No file path contains `wake`; the only hits are the protocol plumbing:
  `chat.ts:434 notifyWakeWordDetected()`, `worker-stack.js:123` (posts `detectWakeWord` to the
  worker), `xiaozhi-model.js:273 detectWakeWord()` (sends `listen{state:'detect',text?}`), and its
  tests. **Nothing anywhere calls `notifyWakeWordDetected`** — not the app behavior, not one of the
  ~45 example MODs.
- There is **no VAD** either: `grep -ri vad firmware/{host,mods}` returns nothing. The client uploads
  continuously whenever `uploading === true` (`flushEncodedAudio`, 20 ms timer).
- The project's own roadmap is explicit: `docs/ROADMAP.md` §"Phase 5: Advanced Capabilities",
  target **April 2027 and beyond**, scope *"Vision-based triggers and recognition / **Wake word
  detection** / …"*, with the dependency rule *"Vision, wake word, and swarming should not proceed
  as primary investments before AI stabilization is complete."* Phase 5's own exit criterion is
  still only a *"wake word architecture decision"*.

**How a turn starts today** (three separate mechanisms, all manual):

| Trigger | Where | Notes |
|---|---|---|
| **Drawer (touch) toggle "Chat"** | `firmware/mods/examples/chat_audioio/mod.js` — `robot.ui.drawer.addDrawerButton({key:'toggleChat', kind:'toggle', callback: startChat/stopChat})`; documented in `firmware/docs/chat-audioio-integration.md` | Opens the WS, sends hello, **immediately** sends `listen{state:'start',mode:auto}`, then it is a continuous streaming session until toggled off |
| **Physical button** | `firmware/mods/examples/ai_stackchan/mod.js`: `robot.input.button.a.onEvent` → `startTalk()` → `robot.audio.record()` (fixed-duration clip) → Whisper → ChatGPT | push-to-talk, clip-at-a-time; not the streaming path |
| **Head touch / petting** | `firmware/host/app/default-behavior/on-context-created.ts` (touch panel gestures, emotions, motion) | cute, not a conversation trigger |

**What "Hey Jarvis" would cost on lane B** — three honest options:

1. **Port esp-sr (WakeNet9 + AFE) into a Moddable native module.** Feasible on paper — the
   Moddable build *is* ESP-IDF, and lane B already ships C modules (`vendor/stackchan-voice`,
   `platforms/m5stackchan-cores3/*.c`) — but it means writing the audio-feed hook, model partition,
   and detection→`notifyWakeWordDetected` wiring in C, and fighting for **~1.8 MB of the 8 MB PSRAM
   already in use** (their soak test: ~6.15 MB free, minimum 6,147,279 bytes). Realistically weeks
   of embedded work, on a target that has **no barge-in to abort anyway** (see §3.7).
2. **Server-side wake word.** Our gateway could detect "Jarvis" in the stream — but the client only
   streams *after* a session is open, so the user must still tap the drawer first, and the mic is
   then open continuously. Wrong ergonomics and a privacy/cost regression.
3. **Keep a manual trigger.** Cheapest and honest: drawer toggle or a button, i.e. **not Jarvis**.

Lane A gives "Jarvis" as a `sdkconfig` line: `CONFIG_SR_WN_WN9_JARVIS_TTS=y`, `CONFIG_SR_WN_WN9_HISTACKCHAN_TTS3`
unset (`firmware/sdkconfig.defaults:13`), with `WAKE_WORD_TYPE` = `USE_AFE_WAKE_WORD` on S3+
(`main/Kconfig.projbuild:687-717`), wake-word data forwarding optional
(`CONFIG_SEND_WAKE_WORD_DATA`), and detection alive in **listening and speaking** states
(`main/application.cc:776-845` — `AbortSpeaking(kAbortReasonWakeWordDetected)` on wake while
speaking, then re-listen). It even sends the wake-word audio so a server can do voiceprint
(`protocol.cc:51-56`). This is the single biggest functional gap between the lanes and it is not
close.

---

## 3. Protocol conformance: lane B's client vs xiaozhi v2.2.4

Read: `xiaozhi-contract.ts` (483 lines, types + validators) and `xiaozhi-model.js` (690 lines, the
worker). Short version: **it is a faithful `version: 1` binary-protocol client** — hello, listen,
abort, mcp, `stt`/`llm`/`tts`, 16 kHz/60 ms uplink, 24 kHz downlink, MCP JSON-RPC 2.0 with
`protocolVersion: "2024-11-05"` (the exact value in `xiaozhi-esp32@v2.2.4 docs/mcp-protocol.md:96`).
Our gateway could serve it. The divergences are all about *who owns the turn*, not the wire format.

### 3.1 Divergence table

| # | Area | v2.2.4 (lane A) | Lane B | Consequence for our gateway |
|---|---|---|---|---|
| 1 | **OTA bootstrap** | `GET <ota_url>` → `firmware{version,url}`, `websocket{url,token}`, `server_time{…}`, optional `activation` | **Absent.** The client takes a literal `ws://`/`wss://` endpoint + Bearer + deviceId/clientId from configuration (`xiaozhi-model.js:60-110`, `parseWebSocketEndpoint`) | Our `/xiaozhi/ota/` endpoint is simply unused by lane B; a lane B mod must hardcode/take the ws URL and token. OTA-based reconfiguration (change URL server-side, no reflash) is a **lane-A-only capability** |
| 2 | **Headers** | `Authorization`, `Protocol-Version: 1`, `Device-Id` (MAC), `Client-Id` (UUID) | Same three/four, `Protocol-Version` hardcoded `'1'` (`:83-88`); `Device-Id`/`Client-Id` only need printable ASCII ≤200 chars — **not MAC-shaped** | Accept arbitrary Device-Id (lane B will not be a MAC unless our mod supplies one); keep the MAC validator out of the required path |
| 3 | **hello** | `version:1`, `transport:"websocket"`, `features:{aec?:true, mcp:true}`, `audio_params 16k/1ch/60ms` (`websocket_protocol.cc:203-226`) | Same shape; `features.mcp` is set when tools exist, `features.aec` only if configured **true** — so by default **no `aec` claim** (`:97-100`, `:152-169`) | Do not assume AEC. Our gateway's AEC/echo assumptions must be lane-specific |
| 4 | **Listening model** | Device opens the channel on wake; `listen start mode` with `auto`/`manual`/`realtime`; **server VAD** in auto | **Starts listening automatically on hello** (`hello():248`), and again after every TTS (`listened()` → `startListening`). **No client VAD.** `mode` is sent but the client does nothing with it locally | Our gateway **must** own end-of-turn detection (it already plans to). Also: a lane B "session" is a long-lived open mic, not a wake→turn→close cycle |
| 5 | **`listen stop`** | Sent when the device stops recording | Only on explicit `stopListening()`; the auto flow just flips `uploading=false` at `tts.start` without a `stop` | Gateway must treat `tts.start` as the implicit end of the utterance (do not wait for `listen stop`) |
| 6 | **`listen detect`** | Real: sent on wake with the wake word text, optionally preceded by wake audio | Type + sender exist (`detectWakeWord`), **never invoked** | Dead code on lane B; our gateway should accept it but not depend on it |
| 7 | **Barge-in** | Wake word during TTS → `abort{reason:"wake_word_detected"}`, mic reopens | `read()` **discards downlink audio while `uploading`** (`:325-326`, matching the spec's "ignore server audio while recording") and the mic is **not** uploaded while TTS plays (`uploading=false` at `tts.start`, `listener` only after playback). So **no voice barge-in at all**; only the drawer toggle / `abort()` | A Jarvis that cannot be interrupted mid-answer is a product regression vs lane A. Fixable only by editing the client (a mod cannot: it is inside the worker) |
| 8 | **Downlink audio params** | Server hello `audio_params` used for the decoder | Accepted, **optional**; defaults to 24 kHz/60 ms if absent; **rejects** sample rates outside {8000,12000,16000,24000,48000} or frame durations outside {10,20,40,60}; max Opus packet 1275 bytes | Send `audio_params{opus,24000,1,60}` in server hello (we do). One packet per binary frame, ≤1275 B |
| 9 | **Ordering rules** | Audio after hello only | `fail('received Opus audio before XiaoZhi hello')`; **must send `tts.start` before any TTS audio** or it is dropped; `tts.sentence_start` **requires** `text` | Strict but same as spec; our gateway already plans `tts.start` first |
| 10 | **`stt`/`llm`** | As spec; `llm` carries `emotion` | As spec; `llm.emotion` + `llm.text` optional strings; `stt.text` required | Fine. Note: input transcript is only displayed if a mod wires `onInputTranscript` (the example comments it out) |
| 11 | **`system` / `custom` / `alert`** | `system.command` (`reboot`), `custom.payload` | All accepted and surfaced to the mod (`system`, `custom`, `alert` handlers); unknown `type`s become a `protocolWarning` instead of a crash | Our `system` channel (reboot) works; nice robustness property |
| 12 | **MCP** | Device is the MCP **server**; server calls `initialize` → `tools/list` → `tools/call`; `protocolVersion 2024-11-05` | Same. Implements exactly those three; answers unknown methods `-32601`, bad params `-32602`; `tools/list` returns `nextCursor: ""`; tool results normalised to `{content:[{type:'text',text}],isError:false}` | Our MCP client must tolerate `nextCursor: ""` and only use the three methods. **Tool set is whatever the mod registers**, not `self.robot.*` (§4) |
| 13 | **Session id** | Device records it, echoes it in events | Same, **and validates**: a server event whose `session_id` ≠ the recorded one is dropped with a warning (`onJSON:179-187`); client events always carry it | One session per connection; never reuse/mix session ids on a live socket |
| 14 | **Text input** | Not defined | `sendText()` warns *"XiaoZhi v1 does not define text input"* | No text channel; our gateway must not expect it |
| 15 | **Glyph push / fonts** | v2.2.4 can push font glyphs (`glyph_push`, `text_font`) | Types/validators exist, but the client **never advertises `text_font` and never renders glyphs** ("Dynamic glyph rendering is not yet connected to the UI. Never advertise it.", `:101`) | Do not send `glyph_push`; plain text bubbles only. CJK glyph streaming is off the table |
| 16 | **Transport security** | TLS to the cloud OTA/ws | Refuses Bearer over plain `ws://` to a **non-private** host; allows it on `10/8`, `127/8`, `169.254/16`, `172.16/12`, `192.168/16` (`isTrustedLocalHost`) | Our `ws://172.16.90.14:8010` + Bearer is explicitly permitted — good |
| 17 | **Reconnect / backpressure** | xiaozhi retries the channel | No auto-reconnect: a failed/closed socket posts `failed`/`disconnected` and the mod must restart the session. Write queue capped at 64 KiB, then the socket is failed (`MAX_QUEUED_BYTES`) | Gateway must be robust to abrupt closes; a lane B mod must own retry |

**Net:** *wire-compatible, turn-model-incompatible.* If we ever serve lane B, the gateway changes
are: accept non-MAC Device-Id, treat `tts.start` as end-of-utterance, own VAD entirely, never send
`glyph_push`, tolerate `nextCursor: ""`, and accept that the mic is open for the whole session.

### 3.2 The one that matters most for Jarvis

Lane B's half-duplex rule (§3.7) plus auto-listen means: **no wake word, no barge-in, an open mic
for the session, and a turn boundary decided by our server.** That is a different product from
"wakes to Jarvis, interrupts, closes". Lane A's `application.cc` wake handling is precisely the
behaviour we specified in the plan.

---

## 4. Our six body tools on lane B: five are a mod, one does not exist

Lane B's MCP tools are **JS objects the mod passes to `ChatService`** —
`{name, description, parameters, execute}` (`chat.ts:100-135`, `chat_audioio/mod.js:186-261`), which
`xiaozhi-model.js:477-485` advertises in `tools/list` and dispatches in `tools/call`. There is no
firmware-level `self.robot.*` equivalent.

| Our tool (lane A, `hal_mcp.cpp`) | Lane B equivalent | Effort / gap |
|---|---|---|
| `self.robot.get_head_angles` | `context.motion.pose` (`{body:{rotation:{y,p,r}}, eyes:{…}}`) — MotionCapability | **Radians, not degrees**; no servo "speed" readback |
| `self.robot.set_head_angles` (yaw ±128°, pitch 0–90°, speed 100–1000) | `context.motion.setPose(pose, timeSeconds)` / `lookAt(vec3)` / `lookAway()` | Yaw limit in the driver is ±128.0° and pitch 5–85° (`m5stackchan-servo.ts`: `angleLimit {min:-1280,max:1280}` and `{min:50,max:850}` in 0.1° units) — **same mechanics, different units and no speed field** (a duration instead) |
| `self.robot.set_led_color` (0–168/channel) | `context.lighting.lightOn(name,r,g,b)`, `lightOff`, `lightBlink`, `lightRainbow`; `led[name].on/off/blink/rainbow` | **0–255**, plus effects lane A lacks. In the **simulator the LED is a stub** (`lighting/wasm/led`) |
| `self.robot.create_reminder` | **Nothing.** `grep -ri reminder firmware/{host,mods}` → no hits | Would be built by us: a timer in the mod, lost on reboot unless persisted to `preferences`/SD-card — and lane B has **no time source to schedule against** (no OTA `server_time`, no SNTP/RTC sync found in the tree) |
| `self.robot.get_reminders` | same | — |
| `self.robot.stop_reminder` | same | — |

So: **head + LED = a mod we write in JS (small); reminders = a mod we design from scratch with no
RTC-backed scheduler.** On lane A all six already exist, are already advertised over MCP, and the
plan's `robot_*` MCP bridge is a pure pass-through. Lane B trades ~0 firmware work for ~all of the
body-tool work, and loses reminders.

Emotion vocabulary also differs: lane A honours
`neutral/happy/laughing/angry/sad/crying/sleepy/doubtful`; lane B's `face-state.ts` has
`NEUTRAL/ANGRY/SAD/HAPPY/SLEEPY/DOUBTFUL/COLD/HOT` — **no `laughing`, no `crying`, plus `cold`/`hot`**.
Our gateway's emotion mapping would need a per-lane table.

---

## 5. Iteration cost: how a change ships on each lane

**Lane B — behaviour/face/UI: MOD, no rebuild (the big win).**

- A MOD is JS/TS + `manifest.json`; `npm run mod -- mods/<x>/manifest.json` builds an XS archive,
  finds the device's `xs` partition, and writes it with `esptool` (`firmware/README.md`,
  `AGENTS.md`, `docs/operations/web-firmware-flash.md`). Host firmware is untouched.
- Same archive installs **from the browser** over WebSerial (`web/editor/esptool-installer.mjs`),
  and the **simulator** loads it into WASM to run it before hardware.
- The **block editor** (`web/editor/`, Blockly) generates that same `mod.js` + manifest — no
  separate runtime, no escape hatch to raw JS ("arbitrary JavaScript input is not added to the
  educational profile", `docs/specs/visual-programming.md`). What it buys us: our *face behaviour
  and reactions* become a visual artefact Cameron can tweak without touching firmware, and the
  generated MOD is the same thing an expert would hand-write, with capability/target diagnostics
  before any flashing.
- **Face work** is Piu/JS: `SimpleFace`/`DogFace`/`ImageFace` + `setEmotion/setEyeOpen/setMouthOpen`
  + sprite hands, or the new **Avatar DSL** (2026-10-20/Nov work: a small DSL compiled to AVBC
  bytecode, run by a bounded C VM with a WASM preview; `web/avatar-dsl/`, `firmware/tools/avatar-dsl/compiler`,
  `firmware/mods/examples/avatar-dsl/`). It ships **as a MOD** ("Build and install this MOD
  separately", `.changeset/avatar-dsl-face-mod.md`).
- Firmware itself: `npm run build` / `npm run flash` via xs-dev + Moddable SDK (pinned XS 17.8.0,
  SDK 9.0.0/10.0.2 depending on doc), **no OTA** — USB serial only.

**Lane A — C++/LVGL: rebuild the world for any behaviour change.** Face = `avatar/skins/default/*`
LVGL classes; a new face or a new body tool = edit C++, `idf.py` build, flash the whole app (OTA
partition exists, but we are the ones who would have to publish it). The *only* fast loop is the
protocol layer, which is our Python client on the host, not the robot.
**This is lane B's real advantage and it is large** — for *face and persona* iteration.

**But** the two lanes do not share the artefact: lane A's skin is LVGL 9 C++, lane B's is Piu JS
(`smooth_ui_toolkit`/`lvgl_cpp` is the vendor's stack, not Moddable's Piu). Porting the Jarvis skin
between lanes is **rewrite, not translate**, in either direction. And lane B's face must be
re-expressed when it changes because of the 2026-11-01 "webtools-face-dsl" rework (v2.0.x is 12
days old).

---

## 6. What lane A should steal from lane B (do this regardless of the verdict)

1. **The `host` / `MOD` split.** The single most portable idea. Even on lane A we could keep the
   face/persona/reactions as data (a JSON/Lua-ish behaviour blob) rather than C++, but that is a
   bigger change than it looks with LVGL. At minimum: keep our Jarvis skin isolated behind one
   interface so it can be re-pointed.
2. **The browser simulator as the skin review surface.** ~0 effort to *try*: `stack-chan`'s
   simulator already runs a MOD-generated face; if we prototype our persona as a lane B MOD we get a
   free, interactive preview of expressions/lip-sync/drawer without hardware. Treat it as a design
   tool, not a runtime.
3. **Half-duplex + server-VAD discipline, made explicit.** Their client hard-codes "do not play
   server audio while uploading" and our gateway should mirror that assumption in its turn logic for
   lane A too (`listen stop` may be implicit; `tts.start` means the utterance ended).
4. **Their MCP-over-the-voice-channel shape** (device = MCP server, three methods, text content
   blocks, `nextCursor: ""`) — already what we planned, but their validator set is a good checklist
   for our gateway tests (`xiaozhi-contract.ts` is effectively a spec conformance suite).
5. **Their web installer/`xs` partition model** — the reason a face change is seconds. Worth
   designing into our lane A build *even if the payload is a C++ blob*: a small "persona/asset"
   partition we can rewrite without reflashing the app.
6. **Their approval model** (`firmware/host/app/remote-session/approval-*.ts`, an in-UI
   allow/deny flow for remote tool calls) — good prior art for letting the robot confirm
   destructive Hermes actions out loud.

## 7. What lane B would need before it could be primary (the "switch" conditions)

Switch only if **all** of these become true — they are the inverse of the gaps above:

1. an on-device wake word (esp-sr port) with wake-during-TTS barge-in, shipped in the host;
2. barge-in while speaking (client change: keep mic + abort on VAD, or accept wake-word-only);
3. a turn model that is not "open mic for the whole session";
4. reminder/state persistence with a clock;
5. `xiaozhiV1` actually exercised by a shipped mod + soak-tested for the mic stall
   (`docs/investigations/moddable-9.5-hardware-2026-09-06.md`: a **17,978 ms** gap between recorded
   mic receptions in a 1,800 s soak; 58/60 pings answered; "low-latency stable audio: verdict
   withheld");
6. a second maintainer / a 1.x stability promise we can point a maintenance budget at.

Until then, lane B is a **feature-velocity and preview lane**, not the bring-up lane.

---

## 8. Plain answers to the five questions

1. **Simulator:** runs the real WASM host app (face, drawer, touch, buttons, servo rotation, camera,
   buffered mic/speaker) and loads MODs; it does **not** run the conversation path — no OTA, no
   WebSocket, no Opus, no `conversation` module in the wasm manifest, Wi-Fi stubbed as
   "unsupported". **Display/MOD-only.**
2. **Wake word:** none on-device, no VAD; a turn starts from the Drawer "Chat" toggle (touch) or a
   physical button (push-to-talk, `ai_stackchan`). "Hey Jarvis" = port esp-sr into a native module
   (weeks, memory-tight) **and** first fix barge-in; or run wake detection server-side behind a
   manually opened session (bad ergonomics). Roadmap says not before Apr 2027.
3. **Protocol:** v1 wire format is conformant (hello/features, listen auto|manual|realtime, detect,
   abort, stt/llm/tts, 16 k↑/24 k↓ 60 ms Opus, MCP JSON-RPC `2024-11-05`, session-id echo +
   validation). Divergences: **no OTA**, non-MAC Device-Id, no `aec` claim, auto-listen on connect
   with **no client VAD**, `tts.start` is the implicit end of utterance, **no barge-in**, downlink
   dropped while uploading, strict audio-param whitelist + 1275 B packets, `tts.start` must precede
   audio, `sentence_start` requires text, no `text_font`/`glyph_push`, no text input, no
   reconnect, 64 KiB write backpressure, Bearer-over-`ws://` only on RFC1918 hosts.
4. **Body tools:** head angles and LED are JS capabilities a MOD registers as MCP tools (radians;
   0–255; pitch limited to 5–85°); **reminders do not exist** and would be our own mod code with no
   reliable clock. Lane A already has all six as firmware MCP tools.
5. **Iteration:** lane B ships behaviour as a JS MOD/`.xsa` written to the `xs` partition over USB
   (or run in the simulator) — host untouched, seconds; the block editor generates the same
   artefact from Blockly, which is a real win for persona/face tweaking. Lane A ships nothing
   without an ESP-IDF rebuild + flash, and its LVGL skin does not port to lane B's Piu.

## 9. Open questions for Cameron

1. **Interruptibility.** Lane A gives "say Jarvis again to cut me off"; lane B cannot without
   editing their client. Is barge-in a v1 requirement? (My read: yes — it is in the plan's Phase 2
   acceptance, which alone keeps lane A primary.)
2. **Do we want a lane B spike at all?** My read: **one MOD**, timeboxed, *after* the robot is
   talking on lane A — it is the only way to get a live face preview before we own an LVGL skin,
   and it costs a few hundred lines of JS (the host already ships the xiaozhi worker and Opus).
3. **Reminders.** On lane A they are free firmware tools; on lane B they are ours. Does the reminder
   feature have to survive a reboot? (If yes, it argues for keeping reminders in the gateway, not on
   the robot, on either lane.)

## 10. Reproducing

```sh
# lane B source (read-only clone used for this note)
git clone --depth 1 https://github.com/stack-chan/stack-chan   # e7e27e2f, 2026-11-01
grep -rn "conversation" firmware/host/platforms/wasm/manifest.json          # absent
grep -n "xiaozhiV1Available" -A2 firmware/host/modules/conversation/manifest.json
grep -rn "notifyWakeWordDetected\|detectWakeWord" firmware --include="*.js" --include="*.ts"
grep -rn "unsupported" firmware/host/modules/connectivity/wasm/network-manager.ts
grep -ri vad firmware/host firmware/mods | wc -l                            # 0

# lane A source (already local)
cd ~/projects/jarvis-hardware/.research_stackchan/StackChan
grep -n "AddTool" firmware/main/hal/hal_mcp.cpp
grep -n "WAKE_WORD_TYPE" -A6 firmware/main/Kconfig.projbuild
cat firmware/repos.json        # xiaozhi-esp32 v2.2.4 + patch
sed -n 776,845p .research_stackchan/xiaozhi-esp32/main/application.cc   # wake → abort → re-listen
```

Nothing in this spike was flashed, built or executed against hardware; lane B's Node tests were not
run here (no `node_modules` in the clone), so every lane B claim above cites the source file that
implements it.
