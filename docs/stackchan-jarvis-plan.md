# StackChan Jarvis — build plan (v2)

**Status:** plan only. Nothing purchased, nothing built. Purchase date: **2026-11-05**
(whole kit if the budget allows).
**Supersedes:** `jarvis-round-display-plan.md`, `jarvis-desktop-hardware-final.md`,
`jarvis-desktop-approach-review.md`, and the RPi4-as-voice-front-end plan in
`~/projects/jarvis`. This device becomes the Jarvis physical front end.
**Extends:** `stackchan-research-report.md` (t_6707addb) — its September conclusion
("needs a xiaozhi-compatible backend") is still true; v1 of this plan answered it with
someone else's stack, and v2 answers it with our own.

## 1. What changed in this revision

Cameron, 2026-10-30: build on the **base StackChan project**, own what we need, do not
depend on the single-maintainer reference project (`dax8it/nous-girl-stackchan`), and make
the robot **an alternate front end to the existing backend on CT 914**.

That removes most of v1's moving parts and puts the code where we can maintain it:

| v1 (dropped) | v2 (this plan) |
|---|---|
| Someone else's firmware fork, beta, one maintainer | **Official `m5stack/StackChan`**, M5Stack-maintained, MIT — we build it ourselves |
| `xiaozhi-esp32-server` in Docker on a new LXC | **Our own gateway service on CT 914**, no Docker, no new CT |
| ~15 GB of local STT/TTS models | Reuse what already exists: **whisper on CT 912:8001**, **edge-tts `en-GB-RyanNeural`** |
| Someone else's `hg` bridge + their MCP server | Our MCP server (thin) over the protocol's own MCP channel |
| GIF face packs (their feature) | Base firmware draws its face in **LVGL/C++ skins** — we skin it |
| Their wake word build | Espressif ships **`wn9_jarvis_tts`** — a stock "Jarvis" WakeNet9 model |

## 2. What this is

A StackChan desk robot that wakes to "Jarvis", answers in Ryan's voice, emotes and turns
its head, and is backed by the same Hermes install, memory and tools as the assistant on
914. The robot carries no brain of its own: it is a face, ears and mouth on the LAN.

Non-goals for v1 of the build: no on-device LLM, no local model, no cloud dependency, no
fork of StackChan's behaviour, no new VLAN, no new container.

## 3. Hardware

Purchase 2026-11-05. Two ways to buy it; the staged route is the default.

| Part | SKU | Price | What it unlocks |
|---|---|---|---|
| StackChan Core (head only) | C156 | $45.00 | 2.0" capacitive touch screen, GC0308 camera, dual mics + amp, 1 W speaker, IMU, proximity/light sensors, microSD, AXP2101 PMIC. Face + voice + camera, USB-C powered |
| StackChan Body | A180 | $55.00 | Servos (360° yaw, 90° pitch, feedback), 12 RGB LEDs, 550 mAh battery, 3-zone head touch, NFC, IR Tx/Rx |
| **StackChan AI Desktop Robot Kit** | K151 | **$99.00** | Core + body in one parcel — cheapest total |
| **…Kit with Remote Control** | K151-R | **$125.00** | As above + IR remote (volume/mute without waking it) |

Spec of the core, from the vendor record: ESP32-S3 (dual Xtensa LX7 @ 240 MHz, 16 MB
flash / 8 MB PSRAM, Wi-Fi + BLE), 320x240 touch LCD, GC0308 camera, ES7210 mic ADC with a
reference channel for AEC, AW88298 speaker amp, BMI270 + BMM150 IMU, microSD, 550 mAh in
the body. 187 g assembled. Both kit variants showed "10+ in stock" on 2026-10-30; the
store is running a site-wide Black Friday banner, so check the cart price.

Notes before splitting the purchase:

- The **body** carries the servos, battery, LEDs and touch head. Nothing else is needed.
- **Do not substitute a plain CoreS3 / CoreS3-SE / Lite** for the C156: M5Stack states the
  StackChan app will not connect to those, and the body mounts the C156 specifically.
- Optional extras: 32 GB microSD ($8-12, music/logs), spare feedback servo pair (~$8).
  Not needed: soldering iron, 3D printer, GPU, cloud account.
- Budget: **$99** (kit) or **$45 now + $55 later**, ~$20 of extras optional.

### Why ESP32-S3 and not something faster

The constraint is firmware, not silicon. Everything — board pins, servo bus, LED harness,
LVGL face, ESP-SR wake word — targets the CoreS3's S3.

| Chip | Muscle | Radio | Verdict |
|---|---|---|---|
| **ESP32-S3** (CoreS3) | dual Xtensa LX7 @ 240 MHz, 8 MB PSRAM | Wi-Fi 4 + BLE 5 | **correct: this is what StackChan is built for** |
| ESP32-P4 | dual RISC-V @ 400 MHz, MIPI-DSI/CSI, H.264 | **none — needs an ESP32-C6 companion** | better screen silicon, wrong device; a separate P4 build later if wanted |
| ESP32-S31 (2026) | dual RISC-V @ 320 MHz, Wi-Fi 6, 1 GbE, 802.15.4 | built in | too new; needs IDF 6.1; no StackChan support |
| ESP32-C6 | weaker, little PSRAM | Wi-Fi 6 + Thread | worse for faces |

## 4. Firmware: the official project, built by us

Base: **`m5stack/StackChan`** @ `main` (MIT, M5Stack-maintained). Its `firmware/` is an
ESP-IDF 5.5.4 project that pulls pinned components via `firmware/repos.json` (mooncake
v2.3.3, mooncake_log v1.5.0, smooth_ui_toolkit v2.12.0, ArduinoJson v7.4.2, esp-now @
`c33383d`) and embeds **xiaozhi-esp32 v2.2.4** with M5's patch.

Everything we need is already a build-time knob. **No source patches to StackChan's
behaviour.** Our delta is one overlay file, `sdkconfig.defaults.local`:

| Config | Stock | Ours | Why |
|---|---|---|---|
| `CONFIG_OTA_URL` | `https://api.tenclass.net/xiaozhi/ota/` | `http://172.16.90.14:8010/xiaozhi/ota/` | point the voice channel at our gateway |
| `CONFIG_STACKCHAN_SERVER_URL` | `http://47.113.125.164:12800` | our gateway (or a dead address) | otherwise the robot phones M5's SaaS for avatar/app features |
| `CONFIG_SR_WN_WN9_HISTACKCHAN_TTS3` | `y` | **unset** | stock M5 wake word |
| `CONFIG_SR_WN_WN9_JARVIS_TTS` | — | **`y`** | stock Espressif WakeNet9 "Jarvis" model, verified present in esp-sr's `Kconfig.projbuild` |
| `CONFIG_USE_HOTSPOT_WIFI_PROVISIONING` | `n` | **`y`** | without it a fresh board has no way onto Wi-Fi (hotspot/blufi/acoustic are all off by default) |
| `CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING` | `n` | `y` (optional) | alternative if the hotspot captive page is awkward |

Notes that come straight out of reading the source:

- `getCheckVersionUrl()` reads `ota_url` from NVS settings first and only falls back to
  the compiled default, so the address is also settable at runtime.
- The device's OTA response parser accepts a minimal document — `firmware{version,url}`,
  `websocket{url,token}`, `server_time{timestamp,timezone_offset}` — and treats
  `activation` as optional. **We omit activation entirely**: no tenclass account, no
  six-digit code, no cloud.
- **The face is code, not GIFs.** `firmware/main/stackchan/avatar/skins/default/` holds
  `eyes.cpp`, `mouth.cpp`, `speech_bubble.cpp`; emotions are `Emotion::Neutral/Happy/
  Angry/Sad/Sleepy/Doubt`. A bespoke Jarvis face is therefore **our own LVGL skin in our
  own repo** — more work than dropping in a GIF pack, but it is our source and it never
  drifts when a stranger rebases his fork.
- The firmware already registers **MCP tools** (`firmware/main/hal/hal_mcp.cpp`), which is
  how the brain moves the body:
  `self.robot.get_head_angles`, `self.robot.set_head_angles` (yaw ±128, pitch 0-90,
  speed 100-1000), `self.robot.set_led_color` (0-168 per channel),
  `self.robot.create_reminder` / `get_reminders` / `stop_reminder`.
- Toolchain: ESP-IDF **5.5.4**, `idf.py` build, `repos.json` fetch step, OTA partition with
  `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` (so a bad flash rolls back).
- **Build the toolchain on 914** (or your desktop) and prove the binary compiles before
  the parcel lands — a source build is a Phase 0 deliverable, not a day-one surprise.

## 5. Backend: the Jarvis Device Gateway (our code, on 914)

A small Python service on CT 914 that speaks the documented xiaozhi WebSocket protocol to
the robot and hands every conversation to the Hermes install that already runs there.

```
StackChan (VLAN 20)                       CT 914, 172.16.90.14
  mic 16 kHz Opus  ──ws :8010──►  gateway: OTA + WS session
  speaker 24 kHz   ◄────────────          │  VAD → Opus decode
  face (LVGL)                             ▼
  servos / LEDs                   whisper STT (CT 912:8001, exists)
                                          │
                                          ▼
                              Hermes API server :8642 (existing, see note below)
                                          │  streamed reply text
                                          ▼
                              edge-tts en-GB-RyanNeural (exists)
                                          │  sentence chunks → Opus 24 kHz → ws
                                          ▼
                              MCP: robot head / LEDs / reminders (tools/call)
```

**Protocol subset we implement** (from `78/xiaozhi-esp32` v2.2.4 `docs/websocket.md`):

| Element | Detail |
|---|---|
| Handshake headers | `Authorization: Bearer <token>`, `Protocol-Version`, `Device-Id`, `Client-Id` |
| Device → server JSON | `hello` (with `features.mcp`), `listen` (`start`/`stop`/`detect`), `abort`, `mcp` (JSON-RPC 2.0) |
| Server → device JSON | `hello`, `stt`, `llm` (`emotion`), `tts` (`start`/`sentence_start`/`stop`), `mcp` |
| Binary | Opus frames, 60 ms; device uplink 16 kHz mono, server downlink 24 kHz |
| Emotions the firmware honours | `neutral`, `happy`, `laughing`, `angry`, `sad`, `crying`, `sleepy`, `doubtful` |
| MCP | server is the client: `initialize` → `tools/list` → `tools/call`, all inside `type:"mcp"` messages |

**How the brain is reached.** Hermes' OpenAI-compatible API server — **live at
`172.16.90.14:8642`, bound to `0.0.0.0`, gated by `API_SERVER_KEY`** (verified 2026-10-30: 401
without the key, 200 with it; nothing listens on 8650, which earlier drafts of this plan
wrongly named). Because the gateway runs on the *same host* it should use
`http://127.0.0.1:8642/v1`, and the bind should eventually be tightened from `0.0.0.0` to
loopback or the host IP — a defence-in-depth change that needs the gateway restarting, so it
is scheduled rather than urgent (`API_SERVER_KEY` remains the real control),
so the robot's words land in the same profile with the same memory, skills and homelab
tools. Loopback only: nothing new is exposed to the LAN except the gateway.

**Body control.** A thin MCP server of ours (stdio, same host) exposes the robot's tools to
Hermes as `robot_get_head_angles`, `robot_set_head_angles`, `robot_set_led_color`,
`robot_reminder_*`, and forwards each call as a `tools/call` on the live robot session.
Idle session → tool returns "robot not connected" rather than blocking.

**What we deliberately do not build:** a StackChan cloud account, the M5 StackChan app,
the Go backend in `server/`, the Flutter apps, or the `stackChan/ws` avatar control
channel. The three things we want from that channel — head, LEDs, reminders — are already
MCP tools on the audio channel.

## 6. Phases

**Phase 0 — no hardware (evenings of 10/31 → 11/4).**
1. Clone `m5stack/StackChan` into `~/projects/jarvis-hardware/stackchan/`; pin the upstream
   commit; write the `sdkconfig.defaults.local` overlay above.
2. Install ESP-IDF 5.5.4 on 914, fetch components, **build the firmware from source**.
   Record binary size, partition layout and the effective config (`idf.py reconfigure`
   + `sdkconfig` diff).
3. Write the gateway skeleton against the protocol doc: OTA endpoint + WS hello + a
   synthetic device client (Python) that plays Opus from a WAV and asserts on the JSON
   it receives. **TDD here:** the synthetic client is the test harness for every later
   phase, and it needs no robot.
4. Measure Hermes API-server time-to-first-token through the gateway (`curl` + timestamps).
5. Spec + task breakdown for the gateway (spec-first, review by another model lineage).

Acceptance: firmware compiles and the config diff is exactly the overlay; synthetic client
completes hello → stt → llm → tts with audio; TTFT measured and written down.

**Phase 1 — hardware bring-up (parcel day).** Flash our build, hotspot-provision Wi-Fi
(point it at 914), confirm the face animates and "Jarvis" wakes it. Rollback: keep the
vendor image; bootloader rollback is enabled anyway.

Acceptance: wakes to "Jarvis", face animates, no traffic to xiaozhi.me or 47.113.125.164
(verify with a packet capture or firewall logs).

**Phase 2 — voice loop.** Gateway VAD → whisper → Hermes → edge-tts, first in text-only
mode (screen shows the reply), then with audio.

Acceptance: ask a question, hear the answer in Ryan's voice; barge-in with the wake word
stops playback.

**Phase 3 — body.** MCP tools into the Hermes profile; head turns to the speaker, LED
mood colour on emotion, reminders.

Acceptance: "Jarvis, look at me" turns the head; "set a reminder in two minutes" fires on
the robot.

**Phase 4 — persona and face.** Our LVGL skin (monocled gentleman-bot), SOUL.md for a
short-spoken butler, emotion mapping from reply tone.

Acceptance: it reads as Jarvis, not as a generic assistant with a British voice.

**Phase 5 — hardening.** Tool allowlist review, destructive-action confirmation rule,
independent review by a different model lineage, runbook (rebuild, reflash, rollback,
token rotation).

## 7. Latency budget

| Stage | Target | Lever |
|---|---|---|
| Wake word (on-device WakeNet9) | 200-400 ms | sensitivity, nothing else |
| End of speech → VAD close | 300-600 ms | silence tail (tunable in our gateway) |
| whisper STT (CT 912) | 200-500 ms | model size on 912, warm worker |
| **Hermes first token** | **800-2000 ms** | prompt/tool size, model — the one that will bite |
| edge-tts first sentence | 200-400 ms | already fast |
| Opus encode + WS | 20-60 ms | negligible |

Keep first audible word under ~3 s, tool-using answers under ~5 s.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Protocol drift when StackChan rebases xiaozhi past v2.2.4 | we pin the commit; the protocol doc is our contract; our synthetic client catches breakage in CI |
| Base firmware has no GIF faces (unlike the reference project) | accepted: our own LVGL skin, our source, Phase 4 |
| Rebuilding someone's ESP-IDF project is fiddly (mooncake, esp-now @ commit) | Phase 0 does it with no hardware in the way, and records the exact command sequence |
| Robot silently phones M5's cloud | `STACKCHAN_SERVER_URL` overridden + verified by capture in Phase 1 |
| Voice in the house driving my full toolset | gateway-side tool allowlist, SOUL destructive-action rule, robot on the trusted VLAN only |
| Audio half-duplex / AEC | device has a reference channel for AEC; if barge-in is poor, start with "wake word stops playback" |
| whisper latency on 912 | pre-warm the model; measure before optimising |

## 9. Security

- Robot on VLAN 20 (LAN-Devices); gateway and Hermes on VLAN 90. The existing
  `AI Lab Ingress` policy already allows VLAN 20 → all of AI-Lab on all ports, so no new
  firewall rule is needed.
- Gateway binds `:8010` on 172.16.90.14; it reaches the brain at `http://127.0.0.1:8642/v1`.
- **Hermes API server is currently bound to `0.0.0.0:8642` and is LAN-reachable but auth-gated**
  (401 without `API_SERVER_KEY`, proven from the RPi4 and a VLAN-90 peer). Binding it to loopback
  or the host IP is a scheduled defence-in-depth change, not a blocker: the gateway is on the
  same host and always talks to it over loopback.
- One shared bearer token in the gateway's `.env` (1Password-sourced), checked on
  handshake; rotate by editing both sides.
- `STACKCHAN_SERVER_URL` points at us, not at `47.113.125.164`.
- Nothing inbound from the WAN. If off-LAN access is ever wanted, it goes behind the
  existing Cloudflare Tunnel + Access, not a port forward.
- Voice is an unauthenticated local interface: anyone in earshot can say "Jarvis". Treat
  the robot exactly like an open microphone into the agent, and keep destructive
  operations behind a spoken confirmation in SOUL.

## 10. Open decisions (defaults if you say nothing)

| # | Decision | Default |
|---|---|---|
| 1 | Buy kit (K151-R/K151) or stage core → body on 11/5 | **kit if budget allows, else C156 core only** |
| 2 | Brain: existing default profile vs a dedicated `jarvis-body` profile | **existing default profile** (the point is one backend); revisit if latency or safety says otherwise |
| 3 | Gateway: our own implementation vs vendoring `xinnan-tech/xiaozhi-esp32-server` | **our own** — ~600 lines we own, TDD'd, no upstream opinions |
| 4 | Face: our LVGL skin vs stock StackChan look | **stock look first**, skin in Phase 4 |
| 5 | Governed SDD build | **yes, from Phase 0**: spec-first, worker implements, review by another lineage, Jarvis merges |
| 6 | Fate of the RPi4 Jarvis front end | **superseded; leave it running until the robot passes Phase 2, then retire** |
| 7 | Forge + repo for the gateway | **OPEN — Cameron creates a private GitHub repo and invites the `hortocam-agents` machine account.** Implementation cards (T001+) wait for that URL + push permission |
| 8 | Hermes API server bind hardening (`0.0.0.0:8642` → loopback/host IP) | **scheduled, not urgent** — needs the gateway process restarted, so it is planned, not done in a live session. `API_SERVER_KEY` is the actual control; reachability is proven and documented |

## 11. Cost and time

| Item | Cost |
|---|---|
| Hardware (kit with remote / kit / staged core) | $125 / $99 / $45 |
| Extras (optional microSD, spare servos) | ~$20 |
| Software | $0 — StackChan is MIT, xiaozhi is MIT, Hermes is ours, whisper and edge-tts already run |
| Model tokens | as usual |

Time: Phase 0 is the long pole (toolchain + gateway skeleton), then roughly half a day per
hardware phase once the parcel lands.

## 12. References and verification log

Upstream:
- Firmware: https://github.com/m5stack/StackChan (MIT, `main`)
- Embedded assistant: https://github.com/78/xiaozhi-esp32 (pinned v2.2.4)
- Protocol spec: `docs/websocket.md` and `docs/mcp-protocol.md` in that repo at v2.2.4
- Wake word models: https://github.com/espressif/esp-sr (`Kconfig.projbuild`)

What was actually read for this plan (2026-10-30):
- `firmware/repos.json`, `firmware/sdkconfig.defaults`, `firmware/main/Kconfig.projbuild`
  (`STACKCHAN_SERVER_URL` default `http://47.113.125.164:12800`; `OTA_URL` default
  `https://api.tenclass.net/xiaozhi/ota/`; provisioning options default off)
- `firmware/main/hal/hal_mcp.cpp` — the six MCP tools above, with their exact limits
- `firmware/main/hal/board/stackchan_display.cc` — `SetEmotion()` accepted strings
- `firmware/main/stackchan/avatar/skins/default/{eyes,mouth,speech_bubble}.cpp`
- `firmware/patches/xiaozhi-esp32.patch` (16 KB — what M5 changes in xiaozhi)
- `78/xiaozhi-esp32@v2.2.4` `docs/websocket.md` (handshake, JSON types, Opus parameters),
  `main/ota.cc` (OTA response keys, `activation` optional),
  `espressif/esp-sr` `Kconfig.projbuild` (`SR_WN_WN9_JARVIS_TTS` = "Jarvis")
- Hermes live install on 914: `API_SERVER_ENABLED`, `API_SERVER_HOST/PORT/KEY/MODEL_NAME`
  present (grep of `~/.hermes/hermes-agent/locales/en.yaml`)

Vendor records (2026-10-30): K151-R $125, K151 $99, C156 $45, A180 $55; both kits 10+ in
stock.
