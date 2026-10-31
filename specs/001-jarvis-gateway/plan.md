# Implementation Plan: Jarvis Device Gateway

**Branch**: none — `~/projects/jarvis-hardware` is not a git checkout (workspace kind `dir`);
the feature lives at `specs/001-jarvis-gateway/`.

**Date**: 2026-10-30

**Spec**: [spec.md](./spec.md)

**Input**: `docs/stackchan-jarvis-plan.md` (v2) §4–§9, and the protocol contract
`78/xiaozhi-esp32@v2.2.4` (`docs/websocket.md`, `docs/mcp-protocol.md`, `main/ota.cc`,
`main/protocols/websocket_protocol.cc`) checked out locally at
`.research_stackchan/xiaozhi-esp32` (tag `v2.2.4`, commit `e77dedb`), plus
`m5stack/StackChan` `firmware/main/hal/hal_mcp.cpp` at commit `1b57655`.

## Summary

One asyncio Python service on CT 914 that terminates the robot's documented voice protocol and
implements the plan's pipeline: OTA manifest → WebSocket hello → VAD → whisper → Hermes (streamed)
→ edge-tts → Opus back. A second, tiny entry point (same package) is the stdio MCP server Hermes
launches, which forwards the robot's six body tools over a loopback UNIX socket to the live
session. A synthetic device client proves the whole loop with no hardware and becomes the
regression harness.

## Technical Context

**Language/Version**: Python 3.11 (present on the host as `~/.local/bin/python3.11`; the system
interpreter is 3.13.5 and is not used). One virtualenv under `gateway/.venv`.

**Primary Dependencies**: `aiohttp` (HTTP + WebSocket server *and* client), `av` (PyAV: Opus
encode/decode and resampling; ships wheels, so no system libopus build), `edge-tts>=7.2` (voice
`en-GB-RyanNeural`), `pytest` + `pytest-asyncio` for tests, `ruff` for lint. No Docker, no
framework beyond aiohttp, no database.

**Storage**: none. Session and turn state is in memory, one process, at most two devices. The only
persisted things are the environment file (token and endpoints) and the systemd unit on disk.

**Testing**: `pytest` with three layers — unit (pure protocol/codec/VAD/chunker), contract
(aiohttp test client against the app, fake upstreams), integration (synthetic device over a real
socket, fake upstreams by default; real whisper/Hermes/edge-tts behind a `live` marker). Tests are
written before the code that satisfies them; a test that cannot be made to fail on wrong input is
not accepted (constitution gate G5).

**Target Platform**: CT 914, Debian-family LXC, `172.16.90.14`, systemd, Python 3.11. Inbound
`:8010` from VLAN 20 only. Outbound: whisper on `172.16.90.12:8001`, loopback brain, Microsoft
speech endpoint for edge-tts.

**Project Type**: single backend service + one stdio sidecar + one test client, in one Python
package.

**Performance Goals**: last voiced frame → first audible word < 3.0 s median / < 4.0 s p90; brain
TTFT 800–2000 ms; body-tool round trip < 300 ms; abort → silence < 150 ms; 256 MB RSS ceiling.

**Constraints**: protocol frozen at xiaozhi v2.2.4 binary version 1; the firmware is untouched;
one shared token; loopback-only brain; no new host or container; the robot's six tools are the
whole body surface; the gateway must never block a Hermes turn on a missing robot.

**Scale/Scope**: one robot today, design ceiling two concurrent sessions; ~1,500 lines of Python
plus ~2,000 lines of tests; four external interfaces (HTTP OTA, WebSocket, MCP over WebSocket,
MCP over stdio).

## Constitution Check

*GATE: must pass before Phase 0 research; re-checked after Phase 1 design.*

**There is no constitution in this repository.** `~/projects/jarvis-hardware` has no `.specify/`
directory, no `.specify/memory/constitution.md`, no `agents/engineer.md`, no `CLAUDE.md`/`AGENTS.md`
and no git metadata (checked 2026-10-30). This plan therefore derives its gates from the documents
the card names as source of truth — `docs/stackchan-jarvis-plan.md` §2 (non-goals), §5 (gateway
design), §9 (security) — and from the card's verified constraints. A follow-up card (created with
this one) initialises the spec-kit structure and authors a constitution so later phases have a
real gate.

| Gate | Source | Status | Evidence |
|---|---|---|---|
| G1 — No Docker, no new container, no cloud account | plan §1, §2; card | PASS | Single systemd unit on CT 914; deps from a venv; no vendor account anywhere in the design |
| G2 — Brain stays on loopback; only `:8010` is exposed | plan §5, §9 | PASS (design) | Gateway binds `0.0.0.0:8010`; brain URL is `127.0.0.1`; MCP forwarding uses a UNIX socket, not a port. Live deviation noted in "Discrepancies" and handled as an operator action, not a design change |
| G3 — One shared token, six-tool allowlist | plan §9 | PASS | FR-002, FR-014; constant-time compare; bridge refuses anything outside the allowlist |
| G4 — Protocol pinned to v2.2.4; no firmware patches | plan §4; card | PASS | Only documented v2.2.4 message types implemented; binary version 1 only; firmware overlay is out of scope and only its two URLs must match |
| G5 — Provable without hardware | plan §6 item 3 | PASS | Synthetic device (FR-017) is a first-class deliverable; all contract tests run headless |
| G6 — Out-of-scope boundaries respected | card | PASS | No firmware, no RPi4, no face, no persona, no vendor channel in this plan |

No gate is waived, so Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-jarvis-gateway/
├── spec.md      # this feature's specification
├── plan.md      # this file
└── tasks.md     # implementation breakdown
```

(Spec-kit's `research.md`, `data-model.md`, `contracts/` and `quickstart.md` are folded into the
sections below, because the project is not spec-kit-initialised; the follow-up card covers
scaffolding for later features.)

### Source code (repository root)

```text
gateway/
├── pyproject.toml                 # deps, ruff/pytest config
├── .env.example                   # every setting, no secrets
├── README.md                      # run + test, links to spec/plan
├── systemd/
│   └── jarvis-gateway.service
├── jarvis_gateway/
│   ├── __init__.py                # version
│   ├── __main__.py                # `python -m jarvis_gateway` → aiohttp app
│   ├── config.py                  # env → frozen dataclass, validation
│   ├── logging.py                 # JSON-lines logging, turn timer
│   ├── protocol.py                # hello/listen/abort/stt/llm/tts builders and parsers
│   ├── jsonrpc.py                 # JSON-RPC 2.0 ids, errors, request/response matching
│   ├── audio.py                   # Opus decode (16k), encode (24k), resample, 60 ms framing
│   ├── vad.py                     # energy VAD with hysteresis and utterance bounds
│   ├── session.py                 # DeviceSession + registry, one per device id
│   ├── ws_app.py                  # handshake, hello, dispatch loop, close codes
│   ├── ota.py                     # /xiaozhi/ota/ manifest
│   ├── stt.py                     # whisper client
│   ├── brain.py                   # Hermes streamed chat client
│   ├── chunker.py                 # sentence segmentation for streaming TTS
│   ├── tts.py                     # edge-tts → PCM 24k → Opus frames
│   ├── turn.py                    # one turn: stt → llm → tts, cancellable
│   ├── emotion.py                 # reply text/tool hints → firmware emotion
│   ├── mcp_client.py              # gateway-as-MCP-client over the robot socket
│   ├── control.py                 # loopback UNIX-socket server + client framing
│   └── bridge.py                  # stdio MCP server Hermes launches
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── fakes/                     # fake whisper / Hermes / edge-tts servers
│   └── fixtures/                  # hello/ota JSON, WAVs, Opus packets, tool schemas
└── tools/
    └── fake_device.py             # synthetic robot (CLI + importable harness)
```

**Structure decision**: one package, one process, one extra entry point (`bridge.py`) that Hermes
launches. The split is by protocol direction and external service, so each file has one reason to
change; `turn.py` is the only place that sequences the pipeline, and `session.py` is the only place
that owns mutable per-device state. Firmware and face work stay outside this tree, as the plan
requires.

## Phase 0: Research Decisions

Each decision below was made here because the plan fixes the *what* but not the *how*. Unknowns
that could not be resolved from the host are marked and become the first task in their story.

| # | Decision | Rationale | Alternatives rejected |
|---|---|---|---|
| D1 | `aiohttp` for HTTP, WebSocket server and client | One dependency covers OTA (HTTP), voice (WS server) and all outbound calls (WS-less HTTP client); async by default; mature WS upgrade with custom headers | FastAPI+uvicorn (two layers, WS header plumbing still hand-rolled); `websockets` + `http.server` (two servers, no shared lifecycle); Go (the plan wants our code in the repo's language, and the brain client is simplest here) |
| D2 | PyAV (`av`) for Opus and resampling | Wheels bundle FFmpeg's libopus and swresample; identical behaviour on dev machine and CT 914; one API for decode, resample and encode | `opuslib` (needs system libopus version parity, ctypes); `ffmpeg` subprocess per turn (process spawn in the latency path); `discord/opus` bindings (heavy) |
| D3 | Energy-based VAD (RMS with hysteresis), not WebRTC VAD | The device has AEC and a reference channel; a quiet room makes RMS adequate; pure Python is deterministic and trivially testable; thresholds are config | `webrtcvad` (C build per host, less deterministic on synthetic fixtures); silero (model download, latency) |
| D4 | Utterance ends on server VAD or device `listen stop`, whichever first | The device's own VAD/manual release is authoritative in manual mode; our VAD is needed in auto mode where the device never sends stop | Server-only (breaks manual mode); device-only (auto mode never closes) |
| D5 | Brain continuity via `X-Hermes-Session-Key: stackchan-<device-id>`, no `X-Hermes-Session-Id` | Read from the live API server source (`gateway/platforms/api_server*.py`): a session key resolves the live conversation by key and scopes memory per channel; without it each request derives a fresh session from the first user message, losing follow-up context | Per-turn `X-Hermes-Session-Id` (requires the gateway to persist ids and handle rotation/compaction); no header (breaks continuity) |
| D6 | Streamed completions (`stream: true`) parsed as SSE; first sentence synthesised as soon as the chunker yields one | TTFT is the budget item that "will bite" (plan §7); batching the reply would add seconds | Non-streamed JSON (simplest, but blows SC-001) |
| D7 | Sentence chunker: split on `. ! ? …` and newline, flush at ≥20 chars, hard-split at 220 chars, flush the tail at end of stream | Short first sentence → earlier first audio; the hard cap keeps one sentence's synthesis under the budget | Word-by-word streaming TTS (many tiny synthesises, worse prosody and more overhead) |
| D8 | TTS via the `edge-tts` library, synthesising one sentence at a time, decoded to PCM 24 kHz then framed to 60 ms Opus | Matches the pinned voice and the plan's "sentence chunks → Opus 24 kHz"; keeps prosody per sentence | CLI subprocess (spawn cost); full-reply synthesis (no streaming); local TTS model (out of scope) |
| D9 | MCP bridge transport: gateway serves a UNIX socket `/run/jarvis-gateway/control.sock` (0660); `bridge.py` is a newline-delimited JSON-RPC stdio server that proxies to it | The plan specifies a stdio MCP server; a socket keeps the bridge stateless and adds no LAN surface, whereas an HTTP port would add a second listener | Bridge inside the gateway process (Hermes launches stdio, not "connect to me"); TCP on loopback (another port to secure, plan says only 8010 is exposed) |
| D10 | The gateway is the MCP *client* of the robot (initialize → tools/list → tools/call) and the MCP *server* to Hermes | Direction is fixed by the protocol doc; the bridge is where the two meet | Exposing device tools directly with a pass-through schema (loses the allowlist and validation) |
| D11 | Session identity: `session_id = uuid4().hex` issued in the server hello; one session per device id, newest wins | Firmware records the session id from hello and echoes it; "newest wins" makes a reboot recoverable without waiting for an idle timeout | Reject second connection (a crashed robot can be locked out for 120 s) |
| D12 | No persistence: conversation continuity is Hermes' job, reminders are the robot's | Both already exist; adding a database would be a second source of truth | Local transcript store (out of scope, and a privacy liability) |
| D13 | Bind `0.0.0.0:8010`, token-gated; MCP control socket never on TCP | VLAN 20 must reach 8010; nothing else is published | Binding the LAN IP only (breaks if the address changes); TLS (the robot's client is plain `ws://`; isolation is the VLAN) |
| D14 | Binary protocol version 1 only | The pinned firmware defaults to it; versions 2/3 add per-frame headers we do not need and would have to parse in the audio path | Implementing 2/3 speculatively (untestable without the device) |
| D15 | `server_time.timestamp` = epoch **milliseconds**, `timezone_offset` = minutes east of UTC (e.g. `-240` for America/New_York in October) | Verified in `main/ota.cc`: `ts += timezone_offset * 60 * 1000`, then the value is divided by 1000 to set the clock — seconds here would put the robot's clock in 1970 and break reminders | Sending seconds (silently wrong); omitting `server_time` (device keeps a bogus clock) |
| D16 | `firmware.version` echoes the version in the device's system-info body, so no upgrade is offered; `firmware.url` may be `""` | The firmware only upgrades when the semver is newer; echoing the device's own version is the honest "no update" answer and needs no firmware registry | Hard-coding a version (drifts when we flash); omitting `firmware` (firmware logs a warning) |
| D17 | Config keys prefixed `JG_` (Jarvis Gateway), loaded from an `EnvironmentFile` at `/etc/jarvis-gateway/` with mode 0600 | One obvious secret location, readable by the systemd unit only, and the same keys in `.env.example` for tests | A settings table in the repo (secrets in git); 1Password at runtime (a network dependency in the start-up path; the token is refreshed by editing the file) |
| D18 | Error policy: never silent, never fatal — one spoken apology line, then a clean turn end | FR-018; a voice interface that goes quiet is indistinguishable from a crash | Closing the socket on error (robot loses its turn and reconnects); retrying inline (blows the latency budget) |

**Unresolved here, resolved by task**: the exact Hermes model alias to send (the server advertises
`hermes-agent`; T011 records what `/v1/models` returns on the live host); the first-sentence
latency of edge-tts on this host (T026 measures it); whether the device's Opus decoder tolerates
frame-size variation (T010 asserts 60 ms framing on our side, so it never has to).

## Phase 1: Design

### Interface Contracts

#### C1 — OTA manifest (`POST|GET /xiaozhi/ota/`)

Request: the firmware's `GetSystemInfoJson()` body on POST (`version`, `mac_address`, `uuid`,
`chip_model_name`, `application{name,version,compile_time,idf_version,elf_sha256}`,
`partition_table`, `flash_size`, `psram_size`, `minimum_free_heap_size`, …); ignored except for
logging and `application.version`. GET carries nothing.

Response `200 application/json`:

```json
{
  "firmware": { "version": "1.0.0", "url": "" },
  "websocket": { "url": "ws://172.16.90.14:8010/xiaozhi/v1/", "token": "<JG_DEVICE_TOKEN>", "version": 1 },
  "server_time": { "timestamp": 1793407350000, "timezone_offset": -240 }
}
```

Rules: no `activation` key ever (no code, no challenge); no `mqtt` key (the device would prefer
MQTT); `websocket.token` is the raw token — the firmware prepends `Bearer ` because it contains no
space; `firmware.version` = the device's own `application.version`, or `JG_FIRMWARE_VERSION` when
the body omits it, so `IsNewVersionAvailable` is false and no upgrade is attempted; the document is
built per request so the time is fresh. Media type `application/json`, no caching headers.

#### C2 — Voice socket (`GET /xiaozhi/v1/`, WebSocket upgrade)

Handshake headers the firmware sends (`main/protocols/websocket_protocol.cc`):

| Header | Value | Gateway rule |
|---|---|---|
| `Authorization` | `Bearer <token>` (firmware adds the scheme) | Required; constant-time compare against `JG_DEVICE_TOKEN`; else HTTP 401 before upgrade, no state |
| `Protocol-Version` | `1` | Required; only `1` accepted (D14), else 426 |
| `Device-Id` | MAC address string | Required; keys the session registry |
| `Client-Id` | UUID, resets on reflash | Required; recorded, not trusted |

Device → server messages (JSON, `type` discriminator): `hello`, `listen{state: start|stop|detect,
mode}`, `abort{reason}`, `mcp{payload: JSON-RPC 2.0}`. Unknown types are ignored with one debug log
(firmware parity).

Server → device messages: `hello{transport, session_id, audio_params}`, `stt{text}`,
`llm{emotion, text}`, `tts{state: start|sentence_start|stop, text?}`, `mcp{payload}`. Every message
carries `session_id`.

Binary: device→server Opus 16 kHz mono, 60 ms (960 samples) per packet, protocol version 1 (no
header). Server→device Opus 24 kHz mono, 60 ms (1440 samples) per packet. Both directions: one
Opus packet per WebSocket binary frame.

Close codes: 1000 normal, 1003 unparseable JSON, 1008 policy (auth/oversize), 1011 upstream fatal.

#### C3 — MCP over the voice socket (gateway is client)

After the device's `hello` with `features.mcp == true`: `initialize` (id 1) → `tools/list` (id 2,
follow `nextCursor` until absent) → later `tools/call` with per-session increasing ids. Deadline
5 s per request; a timed-out request fails only that call. Tool-call results and errors are passed
through unchanged (`result.content[]`, `result.isError`, or `error{code,message}`).

The six firmware tools (from `hal_mcp.cpp`, exact names and limits):

| Wire name | Arguments | Limits |
|---|---|---|
| `self.robot.get_head_angles` | — | returns `{"yaw": int, "pitch": int}` |
| `self.robot.set_head_angles` | `yaw`, `pitch`, `speed` (all int) | yaw −128…128, pitch 0…90, speed 100…1000 (default 150); `-9999` = leave axis |
| `self.robot.set_led_color` | `red`, `green`, `blue` (int) | each 0…168 |
| `self.robot.create_reminder` | `duration_seconds` int, `message` str, `repeat` bool | 1…86400 s; empty message → "Time's up!" |
| `self.robot.get_reminders` | — | returns JSON array of `{id, duration_ms, message, repeat}` |
| `self.robot.stop_reminder` | `id` int | id from `get_reminders` |

#### C4 — MCP to Hermes (bridge, stdio)

`python -m jarvis_gateway.bridge` speaks newline-delimited JSON-RPC 2.0 on stdin/stdout:
`initialize` (protocolVersion `2024-11-05`, serverInfo name `jarvis-robot`), `tools/list` (the six
bridge tools), `tools/call`. Bridge names and forwarding map:

| Bridge tool (Hermes sees) | Forwards to |
|---|---|
| `robot_get_head_angles` | `self.robot.get_head_angles` |
| `robot_set_head_angles` | `self.robot.set_head_angles` |
| `robot_set_led_color` | `self.robot.set_led_color` |
| `robot_create_reminder` | `self.robot.create_reminder` |
| `robot_get_reminders` | `self.robot.get_reminders` |
| `robot_stop_reminder` | `self.robot.stop_reminder` |

Argument validation happens in the bridge (fail fast, readable message); transport failure or no
live session → `isError: true`, content `"robot not connected"`, returned immediately. Unknown
name → JSON-RPC `-32601`. The bridge must never wait on a dead socket: connect with a 200 ms
timeout, else return the not-connected error.

#### C5 — Control socket (gateway ↔ bridge)

`AF_UNIX` stream at `/run/jarvis-gateway/control.sock` (dir 0750, socket 0660, group `hermes`).
Request: one JSON object per line `{"id": <int>, "method": "tools/list"|"tools/call",
"params": {...}}`. Response: one JSON object per line `{"id": <int>, "result": ...}` or
`{"id": <int>, "error": {"code": int, "message": str}}`. A missing socket, a refused connect, or
`tools/call` with no live session yields `error.code = -32000`, message `robot not connected`.
No authentication on the socket: filesystem permissions are the boundary (same UID as the gateway).

#### C6 — Upstream contracts (consumed, not owned)

- **whisper**: `POST http://172.16.90.12:8001/v1/audio/transcriptions`, multipart `file` (WAV,
  16 kHz mono PCM16), optional `model=whisper-1`, `response_format=json` → `{"text": "..."}`.
  Verified against the live OpenAPI document on 2026-10-30.
- **Hermes**: `POST {JG_HERMES_BASE_URL}/v1/chat/completions`, `Authorization: Bearer
  <JG_HERMES_API_KEY>`, `X-Hermes-Session-Key: stackchan-<device-id>`, body
  `{"model": "<advertised>", "stream": true, "messages": [...]}` → SSE `data:` chunks with
  `choices[0].delta.content`, terminated by `data: [DONE]`. Non-2xx → apology path.
- **edge-tts**: library call with voice `en-GB-RyanNeural`, MP3 chunks → decoded to PCM.

### State machine

Per session:

```text
CONNECTED --hello/hello--> IDLE
IDLE --listen start--> LISTENING
LISTENING --(vad end | listen stop | 30 s cap)--> THINKING
THINKING --stt text--> SPEAKING (tts start … stop) --turn end--> IDLE
THINKING | SPEAKING --abort | listen detect--> CANCELLING --> IDLE
any --disconnect / idle 120 s / protocol error--> CLOSED
```

Invariants: audio frames are only accepted in `LISTENING` (frames in `SPEAKING` are dropped, so a
late device frame cannot corrupt a turn); exactly one turn task per session; `tts stop` is sent if
and only if `tts start` was sent for that turn; a cancelled turn never writes to the socket after
its cancellation completes.

### Data model

```text
DeviceSession
  device_id: str            # Device-Id header
  client_id: str            # Client-Id header
  protocol_version: int     # must be 1
  session_id: str           # uuid4 hex, sent in server hello
  state: CONNECTED|IDLE|LISTENING|THINKING|SPEAKING|CANCELLING|CLOSED
  tools: list[ToolDescriptor]      # from tools/list
  pending_calls: dict[int, Future]  # JSON-RPC id → waiter
  next_rpc_id: int
  utterance: Utterance|None
  turn: Turn|None
  last_activity: float
  created_at: float

Utterance
  pcm: bytearray            # 16 kHz mono PCM16, capped (30 s ⇒ 960 KB)
  started_at, ended_at: float
  voiced_ms: int

Turn
  utterance: Utterance
  transcript: str
  emotion: str              # one of the firmware set
  reply_parts: list[str]    # streamed deltas
  sentences_sent: int
  timings: TurnTimings      # vad_end_ms, stt_ms, ttft_ms, first_audio_ms, tts_end_ms
  cancel: asyncio.Event

ToolDescriptor            # name, description, input_schema (verbatim from device)
ToolInvocation            # rpc_id, name, arguments, deadline, result|error
Config                    # see configuration reference
```

Validation rules live with the boundary that owns them: config at start-up (fail fast), handshake
at upgrade, JSON-RPC arguments in the bridge, audio framing in `audio.py`.

## Latency Budget Mapping

| Plan stage | Target | Where it is won in this design | How it is measured |
|---|---|---|---|
| Wake word (device) | 200–400 ms | device-side WakeNet9; not ours | out of gateway scope (Phase 1) |
| End of speech → VAD close | 300–600 ms | `JG_VAD_SILENCE_MS=300` default, 5 × 60 ms frames | `vad_end_ms` |
| whisper STT | 200–500 ms | one HTTP call, warm service, no retry in the path; WAV header built in memory | `stt_ms` |
| Hermes first token | 800–2000 ms | streamed completion, short system prompt, session key reuse | `ttft_ms` |
| edge-tts first sentence | 200–400 ms | sentence chunker flushes at 20 chars; first sentence synthesised while later text arrives | `first_audio_ms − ttft_ms` |
| Opus encode + WS | 20–60 ms | framing at 60 ms in `audio.py`, no per-frame allocation churn | `tts_end_ms − first_audio_ms` vs frames sent |

## Configuration Reference

All via environment (systemd `EnvironmentFile`), prefix `JG_`:

| Key | Default | Notes |
|---|---|---|
| `JG_BIND_HOST` / `JG_BIND_PORT` | `0.0.0.0` / `8010` | the only inbound listener |
| `JG_PUBLIC_BASE_URL` | `http://172.16.90.14:8010` | used to build the WebSocket URL in the OTA manifest |
| `JG_WS_PATH` | `/xiaozhi/v1/` | must match the URL handed to the device |
| `JG_OTA_PATH` | `/xiaozhi/ota/` | must match `CONFIG_OTA_URL` in the firmware overlay |
| `JG_DEVICE_TOKEN` | — (required) | shared bearer token; rotate in the env file only |
| `JG_FIRMWARE_VERSION` | `1.0.0` | fallback `firmware.version` when the OTA request body omits it |
| `JG_STT_URL` | `http://172.16.90.12:8001` | whisper base URL |
| `JG_STT_TIMEOUT_S` | `10` | |
| `JG_HERMES_BASE_URL` | `http://127.0.0.1:8642/v1` | live host value; the card says 8650 — see Discrepancies |
| `JG_HERMES_API_KEY` | — (required) | same key the API server expects |
| `JG_HERMES_MODEL` | `hermes-agent` | recorded from `/v1/models` |
| `JG_HERMES_TIMEOUT_S` | `30` | hard cap on a turn's brain call |
| `JG_VOICE` | `en-GB-RyanNeural` | |
| `JG_TTS_TIMEOUT_S` | `15` | per sentence |
| `JG_VAD_SILENCE_MS` | `300` | |
| `JG_VAD_MIN_SPEECH_MS` | `250` | |
| `JG_MAX_UTTERANCE_S` | `30` | |
| `JG_SESSION_IDLE_S` | `120` | |
| `JG_CONTROL_SOCKET` | `/run/jarvis-gateway/control.sock` | |
| `JG_LOG_TRANSCRIPTS` | `false` | privacy default (FR-016) |
| `JG_APOLOGY_TEXT` | `"Sorry, I didn't catch that."` | spoken on upstream failure |

## Deployment

`systemd/jarvis-gateway.service`: `User=hermes`, `WorkingDirectory=/home/hermes/projects/
jarvis-hardware/gateway`, `EnvironmentFile=/etc/jarvis-gateway/env`, `RuntimeDirectory=
jarvis-gateway` (mode 0750), `ExecStart=…/.venv/bin/python -m jarvis_gateway`, `Restart=on-failure`,
`NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=full`, `MemoryMax=512M`. The bridge is *not*
a service: Hermes launches it per its own `mcp_servers` entry, and the snippet for that entry is
part of the runbook deliverable.

## Verification Strategy

| Criterion | Evidence produced |
|---|---|
| SC-001, SC-002, SC-003 | `docs/latency-baseline.md` from real turns (T028, T057, T059) with per-stage numbers and the command used |
| SC-004 | `pytest -m "not live"` 20-run loop test in `tests/integration/test_loop.py` |
| SC-005 | `tests/integration/test_barge_in.py` timing assertions over 20 iterations |
| SC-006 | Gateway outbound-connection log assertion (no vendor hosts) + Phase 1 packet capture (firmware workstream) |
| SC-007 | `tests/contract/test_auth.py` parameterised over missing/empty/wrong/malformed tokens |
| SC-008 | `tests/integration/test_soak.py` (marked `slow`), RSS sampled via `resource`/`/proc` |
| SC-009 | `tests/contract/test_bridge.py` latency assertions against a fake session and a dead socket |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Live Hermes API server is bound to `0.0.0.0:8642`, not loopback, and on a different port than the card states | Security (plan §9 intent) and a broken default URL | Base URL is configuration; T011 records the live value; operator action flagged in the handoff (not silently changed here) |
| Codec drift: PyAV wheel unavailable for the host's Python | Blocks the audio path | T009 proves import + encode/decode on CT 914 before anything depends on it; fallback is `opuslib` behind the same `audio.py` interface (D2) |
| edge-tts latency or the Microsoft endpoint blocking us | SC-001 miss | Measure per sentence in T026; chunker already yields short first sentences; apology path keeps the turn honest |
| Energy VAD false-starts on TV/noise | Premature end of speech, repeated turns | Thresholds configurable; `min_speech_ms` and a 30 s cap bound the damage; barge-in gives the user a manual exit |
| Device sends binary protocol version 2/3 after a firmware change | Unparseable audio | Protocol-Version is required and only 1 is accepted, so the failure is loud at handshake rather than silent |
| A robot reboot leaves a half-dead session | Tool calls to nowhere | Newest-wins registry (D11) + 200 ms bridge connect timeout |
| Reminder clock skew | Reminders fire at the wrong time | OTA manifest sends epoch ms + minutes offset (D15); contract test asserts the pairing |
| Hermes turn outlives the budget (big prompt, slow tool) | User waits | Streaming starts speech at first sentence; `JG_HERMES_TIMEOUT_S` caps the turn; latency numbers recorded so the prompt/tool budget can be tuned where it belongs |

## Discrepancies and Open Questions (recorded 2026-10-30, while writing this plan)

1. **Brain port.** The card and plan §5 say `127.0.0.1:8650`. The live host's
   `/home/hermes/.hermes/.env` sets `API_SERVER_PORT=8642`, `API_SERVER_HOST=0.0.0.0`, and
   `http://127.0.0.1:8642/v1/models` answers `200` with model id `hermes-agent`; `:8650` refuses
   connections. The gateway treats the base URL as configuration and defaults to the live value;
   the difference must be reconciled in the runbook, not compiled in. No design change follows.
2. **Brain exposure.** `API_SERVER_HOST=0.0.0.0` means the API server, not just the gateway, is
   reachable from the LAN, which contradicts plan §9 ("loopback only: nothing new is exposed to
   the LAN except the gateway"). This plan does not change a live service; the finding is handed
   to the operator with the recommendation `API_SERVER_HOST=127.0.0.1` before Phase 2.
3. **No project scaffolding.** `~/projects/jarvis-hardware` has no `.specify/`, no
   `.specify/memory/constitution.md`, no `agents/engineer.md`, no `CLAUDE.md`/`AGENTS.md` and no git
   metadata. This plan follows the named source documents instead; a follow-up card initialises
   spec-kit and authors the constitution before implementation workers are dispatched.
4. **Whisper contract confirmed, not assumed.** CT 912 answers `/openapi.json` with
   `homelab-whisper 0.1.0`: `POST /v1/audio/transcriptions` (multipart `file`, optional `model`,
   `language`, `response_format`) and `GET /health`. The plan's "whisper on 912:8001" is therefore
   an OpenAI-compatible endpoint; C6 records it exactly.
5. **edge-tts presence.** No `edge-tts` executable is on `PATH` on CT 914; the Python package
   `edge_tts 7.2.7` is present in the uv cache. The gateway therefore depends on the library, and
   T026 pins/verifies the version in the venv rather than shelling out.
6. **Checkout provenance.** `.research_stackchan/xiaozhi-esp32` is at tag `v2.2.4` (`e77dedb`), which
   is exactly the pinned contract; `.research_stackchan/StackChan` is at `1b57655` (2026-08-19,
   untagged). Tool schemas were taken from that checkout; the firmware workstream's pinned commit
   must be checked against it when the firmware branch is cut.
7. **Two timers, one clock.** The device sets its clock from our OTA response; the gateway's
   reminder-related answers therefore depend on the manifest being right. This is why the OTA
   contract test asserts seconds-vs-milliseconds explicitly — it is the failure most likely to be
   invisible until a reminder fires immediately.

## Complexity Tracking

No constitution violations; nothing to justify. The single deliberate complexity — a second entry
point (`bridge.py`) rather than an in-gateway MCP server — is forced by Hermes launching stdio
servers itself (D9), not chosen for its own sake.
