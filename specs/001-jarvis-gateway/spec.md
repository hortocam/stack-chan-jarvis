# Feature Specification: Jarvis Device Gateway

**Feature Branch**: n/a — `~/projects/jarvis-hardware` is a plain directory (no git checkout, no
`.specify/`); this spec lives at `specs/001-jarvis-gateway/`.

**Created**: 2026-10-30

**Status**: Draft — Phase 0 deliverable, for review by a different model lineage (plan §6 item 5,
§10 decision 5).

**Source of truth**: `docs/stackchan-jarvis-plan.md` (v2), §4 firmware overlay, §5 gateway design,
§6 Phase 0, §7 latency budget, §9 security. The protocol contract is
`78/xiaozhi-esp32` v2.2.4 `docs/websocket.md` + `docs/mcp-protocol.md` (checked out at
`.research_stackchan/xiaozhi-esp32`, tag `v2.2.4`, commit `e77dedb`).

**Input**: Card `t_d1d7c022` — "Run `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` for the
**Jarvis Device Gateway** (xiaozhi-protocol front end for the StackChan robot, running on CT 914)."

## Context

The StackChan robot has no brain of its own. It wakes to "Jarvis" on-device, streams 16 kHz Opus
microphone audio over a documented WebSocket protocol, renders JSON control messages and 24 kHz
Opus speech, and exposes six MCP tools for its head, LEDs and reminders. Today the other end of
that protocol is a vendor cloud; this feature replaces it with a service we own on CT 914 that
hands every conversation to the Hermes install already running there.

The gateway is the only new thing on the network: one Python process on `:8010`, reachable from
VLAN 20. It answers the robot's boot-time configuration request (so the robot needs no account and
never phones a vendor), runs the turn — end of speech → whisper on CT 912 → Hermes on loopback →
edge-tts in Ryan's voice → Opus back to the robot — and carries the robot's six body tools to
Hermes over the same session. Whisper, the brain, the voice and the memory already exist and are
reused unchanged; nothing in this feature writes to them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A spoken question, answered aloud (Priority: P1)

Cameron says "Jarvis, what's the weather like?" The robot wakes, streams what he said, shows the
transcript on its face, and answers in Ryan's voice with a matching expression. He asks a
follow-up without re-pairing anything, and the robot answers in the same conversation, with the
same memory as the assistant on his desktop.

**Why this priority**: This is the product. Every other story is either a precondition for it
(bootstrap, body) or a defence around it (barge-in, operations). It is also the only story that can
be built and proven before the parcel arrives, because the gateway's peer can be a synthetic
device playing a recorded WAV.

**Independent Test**: With no robot present, a synthetic device client opens the session, sends a
recorded utterance, and observes, in order: server `hello`, `stt` with text, `llm` with an emotion,
`tts start`, `tts sentence_start`, binary audio frames, `tts stop`. The transcript and reply text
come from the real whisper and the real Hermes server; only the device is synthetic. Repeatable 20
times without manual intervention.

**Acceptance Scenarios**:

1. **Given** a connected session and the gateway healthy, **When** the synthetic device sends
   `listen start` followed by a recorded question, **Then** within 3 seconds of the last audio
   frame the device receives `stt` whose text matches the recording, followed by `llm` and then
   `tts start` before any audio frame.
2. **Given** a `tts start` has arrived, **When** the first audio frame arrives, **Then** every
   subsequent binary frame is valid Opus at 24 kHz mono, 60 ms per frame, and the frames end with
   exactly one `tts stop`.
3. **Given** the brain replies in several sentences, **When** the first sentence is complete,
   **Then** its audio starts before the remaining sentences have finished being synthesised (the
   turn streams; it does not batch the whole answer).
4. **Given** the robot is speaking and a follow-up arrives after `tts stop`, **When** the device
   sends `listen start` again, **Then** the second turn completes with the same session id and the
   conversation keeps its context.
5. **Given** the user said nothing (silence), **When** the utterance window closes, **Then** no
   `stt`, no `llm` and no audio are emitted, and the session remains usable for the next question.

---

### User Story 2 - A freshly flashed robot bootstraps with no cloud account (Priority: P1)

A newly flashed StackChan, provisioned onto Wi-Fi, asks the gateway for its configuration. It
receives a working voice endpoint, the token to present, and the correct time. It is never asked
for an activation code, never contacts a vendor host, and its reminders fire at the right time.

**Why this priority**: Without it the robot cannot reach the gateway at all, and the whole point of
v2 is to remove the cloud dependency. It is provable with `curl` plus the recorded system-info body
from the firmware, so it costs almost nothing before hardware exists.

**Independent Test**: POST a recorded firmware system-info body to the OTA path on `:8010`; assert
the response has `firmware.version` equal to the device's current version (so no upgrade is
offered), `websocket.url` pointing at the gateway, a non-empty `websocket.token`,
`server_time.timestamp` within one second of now and `server_time.timezone_offset` matching the
host, and that `activation` and `mqtt` are absent. Repeat with an unknown MAC address: same shape,
still no activation.

**Acceptance Scenarios**:

1. **Given** any well-formed system-info body, **When** it is POSTed to `/xiaozhi/ota/`, **Then**
   the response is `200 application/json` and contains exactly the four blocks the firmware reads:
   `firmware{version,url}`, `websocket{url,token}`, `server_time{timestamp,timezone_offset}` and no
   others.
2. **Given** the robot firmware version is the version we shipped, **When** the OTA response is
   read, **Then** `firmware.version` equals it, so the device does not attempt an upgrade.
3. **Given** the token returned by OTA, **When** the robot presents it as `Authorization: Bearer`
   on the voice socket, **Then** the handshake succeeds without any other provisioning step.
4. **Given** the host clock is correct, **When** the robot applies `server_time`, **Then** a
   reminder created for "in two minutes" fires two minutes later (within one second), because the
   timestamp is epoch milliseconds and the offset is in minutes.
5. **Given** the same endpoint receives a GET (the firmware's fallback method when it has no system
   info), **When** it is served, **Then** the response is the same shape.

---

### User Story 3 - Interrupting the robot (Priority: P2)

While the robot is mid-answer, Cameron says "Jarvis" again (or taps stop). The voice stops
immediately, the face leaves the speaking state, and the next question is understood. A long answer
does not hold the room hostage.

**Why this priority**: Half-duplex voice without barge-in is a walkie-talkie. The device sends an
explicit abort, so this is cheap to build correctly — but it is not needed to prove the turn
works, so it follows the two P1 stories.

**Independent Test**: The synthetic device plays a synthetic answer, sends `abort` with
`reason: wake_word_detected` 500 ms in, and asserts that no binary frame arrives more than 150 ms
after the abort, that exactly one `tts stop` follows, and that a subsequent turn completes
normally.

**Acceptance Scenarios**:

1. **Given** audio is streaming, **When** `abort` arrives, **Then** frame delivery stops within
   150 ms, the upstream synthesis is cancelled, and `tts stop` is sent exactly once.
2. **Given** `abort` arrives while the gateway is still transcribing or waiting for the brain,
   **When** it is processed, **Then** no `tts` messages and no audio are sent for that turn, and the
   session stays open.
3. **Given** the robot reports a wake word via `listen state=detect`, **When** the gateway is
   speaking, **Then** it behaves as an abort and is ready for the new utterance.
4. **Given** a second `abort` arrives with nothing in flight, **When** it is processed, **Then**
   nothing is sent twice and no error is logged at warning level or above.

---

### User Story 4 - The brain moves the body (Priority: P2)

"Jarvis, look at me" turns the head. "Set a reminder in two minutes" creates one on the robot, and
it fires there. A sad answer dims the LEDs. Hermes reaches the body through six tools that map
one-to-one onto what the firmware already exposes — nothing new is invented on the device.

**Why this priority**: It is the difference between a speaker and a robot, but it depends on a live
session (US1) and on Hermes' MCP client, and it can be added without changing the audio path.

**Independent Test**: With a synthetic device attached, invoke each of the six bridge tools from a
test MCP client; assert a `tools/call` with the identical name and arguments appears on the robot
socket and that its result reaches the caller. Then disconnect the device and invoke again: the
call returns an immediate "robot not connected" error rather than hanging.

**Acceptance Scenarios**:

1. **Given** a session where the device advertised MCP, **When** the session is established,
   **Then** the gateway discovers the tool list once and serves it to Hermes without re-querying
   per call.
2. **Given** Hermes calls a body tool, **When** the call is forwarded, **Then** the arguments are
   validated against the firmware's limits before transmission, and an out-of-range value is
   refused with a readable message instead of being sent.
3. **Given** the device is idle or disconnected, **When** Hermes calls a body tool, **Then** the
   caller gets "robot not connected" within 50 ms and the Hermes turn continues.
4. **Given** the device answers a tool call, **When** the result returns, **Then** the text content
   and error flag are passed through unchanged, and the JSON-RPC id is matched to the request (no
   cross-talk between concurrent calls).
5. **Given** Hermes requests a tool that is not one of the six, **When** the request is handled,
   **Then** it is refused as "method not found" and nothing is sent to the robot.

---

### User Story 5 - Running it without surprises (Priority: P3)

Cameron can see that the gateway is alive, how slow the last turn was at each stage, and that
someone else on the LAN cannot open a session. The token rotates by editing two files. Nothing
spoken in the room ends up in a log.

**Why this priority**: Needed before the robot lives in the house and before anyone else runs a
turn, but not needed to prove the loop.

**Independent Test**: With 20 bad-token handshakes, all are rejected and no session is created;
a health request reports uptime, version and session count; one completed turn emits one structured
timing record with every stage; a log capture from that turn contains neither the spoken text nor
the token.

**Acceptance Scenarios**:

1. **Given** a missing, empty or wrong bearer token, **When** the WebSocket handshake is attempted,
   **Then** it is refused before the protocol starts, 20 times out of 20.
2. **Given** a completed turn, **When** the log is read, **Then** it contains one record with
   `vad_end_ms`, `stt_ms`, `ttft_ms`, `first_audio_ms` and `tts_end_ms`, and no transcript or reply
   text unless transcript logging was explicitly enabled.
3. **Given** the service is running, **When** `/healthz` is requested, **Then** it reports 200 with
   version, uptime and active session count.
4. **Given** an idle hour with no robot connected, **When** the process is sampled, **Then** memory
   growth is under 20 MB and the service is still healthy.

---

### Edge Cases

- **Hello never arrives** within 10 s of the socket opening: close with a protocol error; create no
  session state.
- **Binary audio before hello**, or a text frame that is not JSON: log once, close with 1003 (or
  1008 for auth-adjacent violations); no partial turn.
- **Unknown JSON `type`**: ignore it (the firmware does the same) and keep the session alive.
- **Duplicate `hello`**, or a second connection for a device that already has a live session: the
  newest connection wins and the older one is closed cleanly, so a rebooted robot is never locked
  out by a half-dead socket.
- **Utterance that never ends**: force end-of-utterance at 30 s so memory cannot grow unbounded.
- **Only noise**: discard under 250 ms of voiced audio; emit nothing.
- **STT returns an empty transcript** (breath, TV): no brain call, no audio, no error surfaced to
  the user.
- **Whisper unreachable or slower than 10 s**: speak a one-line apology and end the turn; the
  session stays usable.
- **Brain unreachable, returns an error, or stalls mid-stream**: speak the apology line for what
  was completed (if anything), end the turn, keep the session.
- **TTS fails for one sentence but not others**: skip that sentence, keep the rest, log the failure;
  if every sentence fails, speak the apology line in a fallback path.
- **Abort arrives during synthesis**: no frames after the abort, exactly one `tts stop`.
- **Device disconnects mid-answer**: synthesis and forwarding stop, the session is released, and a
  later reconnect starts a fresh session without leaked tasks.
- **Frame larger than the sanity cap** or more than 64 KB of JSON: reject and close; never buffer.
- **Clock skew**: the robot sets its clock from OTA; a reminder must not fire in the past because
  the gateway sent seconds where the firmware expects milliseconds.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gateway MUST answer the robot's boot-time configuration request at
  `POST /xiaozhi/ota/` (and `GET` for the firmware's no-system-info fallback) with a JSON document
  containing `firmware.version` equal to the device's current version and a `firmware.url`, a
  `websocket` object with the gateway's voice URL and a non-empty token, and `server_time` with
  epoch milliseconds and a timezone offset in minutes. It MUST NOT include an `activation` or
  `mqtt` block, so the firmware never shows an activation code.
- **FR-002**: The gateway MUST authenticate every voice socket at handshake with a single shared
  bearer token, compared in constant time, and MUST reject a missing, malformed or wrong token
  before any protocol message is exchanged. A rejected handshake MUST NOT create session state.
- **FR-003**: The gateway MUST require the handshake headers the firmware sends (protocol version,
  device id, client id), record the device id, and reply to the device's `hello` with a server
  `hello` carrying `transport: websocket`, a session id, and downlink audio parameters of Opus,
  24 kHz, mono, 60 ms. If no valid `hello` arrives within 10 s, the socket MUST be closed.
- **FR-004**: The gateway MUST accept the device's binary uplink as 16 kHz mono Opus, 60 ms per
  packet, decode it to PCM, and keep per-utterance audio bounded (memory cannot grow with call
  length).
- **FR-005**: The gateway MUST decide when the user has finished speaking using both signals the
  device offers — server-side voice-activity detection and the device's own `listen stop` — and
  MUST end the utterance on whichever comes first, with a configurable silence tail (default
  300 ms), a minimum voiced duration (default 250 ms) and a hard maximum (default 30 s).
- **FR-006**: The gateway MUST transcribe the completed utterance with the existing whisper service
  on CT 912 and MUST send the resulting text to the device as an `stt` message before the brain is
  consulted. An empty transcript MUST end the turn with no further messages.
- **FR-007**: The gateway MUST send the utterance to the Hermes API server on loopback as a
  streamed chat completion, scoped to a stable per-device conversation key so follow-up questions
  keep context and memory, and MUST record time-to-first-token for the turn.
- **FR-008**: The gateway MUST stream the reply as speech: `tts start`, then per sentence
  `tts sentence_start` with the sentence text, then Opus frames at 24 kHz mono 60 ms, then exactly
  one `tts stop`. The first sentence MUST be synthesised and sent before the last sentence of the
  reply has been received from the brain.
- **FR-009**: The gateway MUST send an `llm` message carrying an emotion from the firmware's
  accepted set (`neutral`, `happy`, `laughing`, `angry`, `sad`, `crying`, `sleepy`, `doubtful`)
  before the first audio frame of a turn, defaulting to `neutral`.
- **FR-010**: On `abort`, or on a wake-word `detect` while speaking, the gateway MUST cancel the
  turn in flight, stop sending audio within 150 ms, emit exactly one `tts stop` if speech had
  started, and remain ready for the next utterance on the same session.
- **FR-011**: The gateway MUST keep at most one live session per device id (a new connection
  replaces the old), close sessions idle for 120 s, and release all tasks, buffers and upstream
  connections when a session ends. Session state MUST NOT be shared between devices except the
  configuration and the brain's memory scope.
- **FR-012**: When the device advertises MCP support, the gateway MUST initialise an MCP session on
  the same socket, fetch the tool list once, and follow the device's pagination if offered.
- **FR-013**: The gateway MUST expose the robot's six body capabilities to Hermes as MCP tools of
  its own (`robot_get_head_angles`, `robot_set_head_angles`, `robot_set_led_color`,
  `robot_create_reminder`, `robot_get_reminders`, `robot_stop_reminder`) and forward each call to
  the live session as a `tools/call`, returning the device's result unchanged.
- **FR-014**: The gateway MUST forward only those six tools with their firmware names, validate
  arguments against the firmware's documented limits before sending, and refuse unknown tools as
  "method not found". A call with no live session MUST return "robot not connected" within 50 ms
  and MUST NOT block the Hermes turn.
- **FR-015**: All endpoints, the shared token, voice, model and timeouts MUST come from environment
  configuration read at start-up; no secret may be compiled into the source, printed, or logged,
  and the service MUST refuse to start with a missing or empty token.
- **FR-016**: The gateway MUST expose `/healthz` (version, uptime, active sessions) and emit one
  structured record per turn with per-stage timings. It MUST NOT log audio, transcript text or
  reply text by default.
- **FR-017**: The gateway MUST ship a synthetic device client that speaks the same protocol from a
  WAV file and can assert the expected message sequence, so the loop can be proven, and regressions
  caught, with no hardware.
- **FR-018**: Every failure after the handshake (transcription, brain, synthesis, device error) MUST
  end in a defined state — a spoken one-line apology where the user is waiting, otherwise silence —
  and MUST leave the session usable. No failure may leave the turn hanging or the socket half-dead.
- **FR-019**: The gateway MUST run as a single process under the host's service manager on CT 914,
  with no container runtime and no new host, and MUST stay within 256 MB resident memory and one
  CPU core for a single conversation.
- **FR-020**: The gateway MUST NOT require or contact any vendor cloud account or vendor endpoint;
  its outbound connections are limited to the transcription service, the loopback brain, and
  speech synthesis. Nothing may be exposed to the WAN.

### Key Entities

- **Device session**: one live robot connection; identified by device id, carrying client id,
  protocol version, session id, current state, discovered tool list and last-activity time. One per
  device; replaced on reconnect; released on disconnect.
- **Utterance**: the bounded audio captured between end-of-wake and end-of-speech, with its start
  and end timestamps and its transcription. Discarded when empty or noise-only.
- **Turn**: one question-to-answer cycle; owns the transcript, the streamed reply, the timings of
  each stage, the emotion chosen and the speech frames sent. Cancellable as a unit.
- **Tool descriptor**: one of the six robot capabilities, with the firmware name, the bridge name
  Hermes sees, the argument ranges, and whether the device advertised it in this session.
- **Tool invocation**: a forwarded call with its JSON-RPC id, arguments, deadline and result or
  error; matched back to the caller by id.
- **Configuration**: the service's runtime settings — bind address, token, brain URL and key,
  transcription URL, voice, timeouts, VAD thresholds — all supplied from outside the code.
- **OTA manifest**: the boot-time document the robot reads: firmware block, websocket block, server
  time block. Its shape is a contract with firmware v2.2.4 and may not grow fields the device does
  not read.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From the last voiced audio frame to the first audible word, the median turn is under
  3.0 s and the 90th percentile under 4.0 s, with whisper warm and the brain warm.
- **SC-002**: A turn that uses a body tool still produces its first audible word within 5.0 s at the
  median.
- **SC-003**: Time-to-first-token from the brain is measured on the standard prompt and written
  down, landing inside the 800–2000 ms the plan budgets; the number is recorded in
  `docs/latency-baseline.md` with the date and conditions.
- **SC-004**: 20 consecutive synthetic-device runs complete hello → stt → llm → tts → stop with no
  manual intervention and no unhandled error in the log.
- **SC-005**: 20 out of 20 aborts stop audio within 150 ms and emit exactly one `tts stop`.
- **SC-006**: A full session produces zero connections to vendor hosts, and the robot boots without
  any activation code; verified from the gateway's own outbound-connection log and, later, a packet
  capture in Phase 1.
- **SC-007**: 20 out of 20 handshakes with a missing, empty or wrong token are refused, and none of
  them creates a session.
- **SC-008**: An hour idle leaves resident memory within 20 MB of its starting value and the health
  endpoint still answering.
- **SC-009**: A body-tool round trip on the LAN completes in under 300 ms, and an idle-session call
  returns "robot not connected" in under 50 ms.

## Assumptions

- One robot, one household, one shared token. Multi-robot fan-out, per-user accounts and
  authorisation beyond the token are out of scope for v1.
- The robot is flashable with our own build of `m5stack/StackChan` at the commit pinned by the
  firmware workstream; the gateway depends only on the protocol documented at xiaozhi v2.2.4 and on
  the six tools already implemented in `firmware/main/hal/hal_mcp.cpp`.
- The device uses binary protocol version 1 (raw Opus frames, no per-frame header). Versions 2 and 3
  are not implemented; the gateway rejects a handshake asking for them rather than mis-parsing
  audio.
- Whisper on CT 912 speaks the OpenAI-compatible transcription API (`multipart file` → JSON `text`)
  — verified live on 2026-10-30 — and accepts 16 kHz mono PCM WAV.
- The Hermes API server is enabled, reachable on loopback, and accepts streamed chat completions
  with the model name it advertises. The card names `127.0.0.1:8650`; the live host currently
  configures `8642` (see plan.md "Discrepancies"), so the base URL is configuration, not a
  constant.
- `edge-tts` with voice `en-GB-RyanNeural` is available on CT 914 as a Python library and is
  permitted to reach Microsoft's speech endpoint; "no cloud account" is respected — no account, no
  key, no vendor integration.
- The robot's clock is set from the gateway's OTA response; reminders are scheduled on the device,
  not by the gateway.
- The household LAN allows VLAN 20 → CT 914:8010 without a new firewall rule, as the plan states.
- The robot is used in a quiet room where an energy-based end-of-speech detector is adequate;
  sensitivity is tunable and the plan accepts "wake word stops playback" as the first barge-in
  behaviour.
- The gateway is deployed on CT 914 by the operator (systemd unit), not by a container platform,
  and CT 914 has outbound access to the transcription and speech services.

## Out of Scope

- Firmware changes, firmware builds, flashing and hardware purchase. The overlay lives in the
  firmware workstream (`sdkconfig.defaults.local`); this feature only defines the URLs and token
  that overlay must point at.
- Any change to the incumbent Raspberry Pi 4 voice front end, which stays running until the robot
  passes Phase 2 of the plan.
- The StackChan mobile app, the vendor Go server, the vendor cloud, the `stackChan/ws` avatar
  control channel, and the LVGL face skin (Phase 4 of the plan).
- Persona/SOUL changes, tool-policy decisions beyond the six-tool allowlist, and destructive-action
  confirmation rules (Phase 5 of the plan).
- Multi-robot support, remote/WAN access, per-user authentication, and telemetry.
- Replacing or tuning whisper, Hermes or edge-tts; the gateway consumes them as they are.

## Dependencies

- **CT 912 whisper** (`http://172.16.90.12:8001/v1/audio/transcriptions`) — existing, verified
  reachable on 2026-10-30.
- **Hermes API server on CT 914** — existing; OpenAI-compatible, bearer-authenticated, streamed
  chat completions. Its base URL, key and advertised model are configuration.
- **edge-tts `en-GB-RyanNeural`** — available on CT 914; no account.
- **Firmware protocol** — `78/xiaozhi-esp32` v2.2.4 `docs/websocket.md`, `docs/mcp-protocol.md`,
  and the six tools in `m5stack/StackChan` `firmware/main/hal/hal_mcp.cpp`.
- **Host** — CT 914, `172.16.90.14`, port 8010 inbound from VLAN 20; systemd for supervision.
