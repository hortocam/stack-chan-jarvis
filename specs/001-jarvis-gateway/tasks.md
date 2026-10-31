# Tasks: Jarvis Device Gateway

**Input**: Design documents from `specs/001-jarvis-gateway/` — `plan.md`, `spec.md`.

**Prerequisites**: plan.md (required), spec.md (required). `research.md`, `data-model.md` and
`contracts/` are folded into plan.md because this project is not spec-kit-initialised.

**Tests**: mandatory for this feature, not optional. Every implementation task below is preceded by
the test that fails without it (plan gate G5, spec FR-017). A task is not done until its test has
been observed failing for the right reason and then passing.

**Organization**: tasks are grouped by user story so each story is independently implementable and
testable. Paths are relative to the repository root `~/projects/jarvis-hardware`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on the other task's output)
- **[Story]**: US1…US5 from spec.md
- Every task names the exact file(s) it creates or changes

## Path Conventions

Single Python service under `gateway/`: package `gateway/jarvis_gateway/`, tests under
`gateway/tests/{unit,contract,integration}`, fakes under `gateway/tests/fakes`, fixtures under
`gateway/tests/fixtures`, the synthetic robot at `gateway/tools/fake_device.py`, the deployment unit
at `gateway/systemd/`, project docs under `docs/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: A runnable package, a pinned toolchain, and the fixtures every later test needs.

- [X] T001 Create the service skeleton in `gateway/`: `pyproject.toml` (requires-python `>=3.11`,
  deps `aiohttp`, `av`, `edge-tts>=7.2`; dev deps `pytest`, `pytest-asyncio`, `ruff`; ruff/pytest
  config with `asyncio_mode = auto`), `jarvis_gateway/__init__.py` (version string),
  `jarvis_gateway/__main__.py` (placeholder that starts nothing yet), `gateway/.gitignore`
  (`.venv/`, `__pycache__/`, `.env`), `gateway/README.md` stub.
- [X] T002 [P] Vendor protocol fixtures in `gateway/tests/fixtures/protocol/` transcribed from
  `.research_stackchan/xiaozhi-esp32` at tag `v2.2.4`: `hello_device.json`, `hello_server.json`,
  `listen_start.json`, `listen_stop.json`, `listen_detect.json`, `abort.json`, `stt.json`,
  `llm.json`, `tts_start.json`, `tts_sentence_start.json`, `tts_stop.json`,
  `mcp_initialize_request.json`, `mcp_initialize_result.json`, `mcp_tools_list_result.json`,
  `mcp_tools_call_request.json`, `mcp_tools_call_result.json`, plus `ota_response.json` matching
  plan C1. No field may be invented: copy shapes from `docs/websocket.md`, `docs/mcp-protocol.md`,
  `main/ota.cc` and `hal_mcp.cpp`.
- [X] T003 [P] Audio fixtures in `gateway/tests/fixtures/audio/`: `utterance_ok.wav` (≈1.5 s,
  16 kHz mono PCM16), `silence_2s.wav`, `noise_burst_100ms.wav`, `long_35s.wav` (generated, not
  committed if large), and `opus_16k_60ms/{001..010}.bin` (ten packets). Add
  `gateway/tests/fixtures/make_audio.py` that regenerates every file with the pinned codec and
  documents the exact command in its docstring.
- [X] T004 [P] `gateway/jarvis_gateway/config.py`: frozen `Config` dataclass, `load_config(env)`
  parsing every `JG_*` key from plan "Configuration Reference", validation (token non-empty, URLs
  parse, positive timeouts, ports in range, `voice` non-empty), and `gateway/.env.example` holding
  all keys with non-secret defaults. Tests in `gateway/tests/unit/test_config.py`: defaults,
  overrides, missing token raises, empty token raises, bad URL raises, unknown keys ignored, and the
  example file parses against the loader.
- [X] T005 [P] `gateway/jarvis_gateway/logging.py`: JSON-lines formatter and a `TurnTimer` that
  records named stages with monotonic deltas, plus `gateway/tests/unit/test_logging.py` asserting one
  object per line, ISO timestamps, stage ordering, and that the transcript field is absent unless
  enabled.
- [X] T006 [P] Fake upstreams in `gateway/tests/fakes/`: `fake_whisper.py`
  (`POST /v1/audio/transcriptions` → configurable text, status, delay, empty-result modes),
  `fake_hermes.py` (`POST /v1/chat/completions`, SSE chunks with configurable token pacing, error
  before/after first token, and assertion that `X-Hermes-Session-Key` was sent),
  `fake_edge_tts.py` (monkeypatchable `synthesize(text) -> mp3 bytes`, configurable failure per
  sentence). Shared pytest fixtures in `gateway/tests/fakes/__init__.py` start them on ephemeral
  ports. Unit tests for each fake's modes.

**Checkpoint**: `pytest gateway/tests/unit` is green; fixtures exist; no feature code yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Protocol codec, audio path, session state, and the HTTP/WS shell every story needs.

**⚠️ CRITICAL**: no user-story work starts until T015 is green.

- [X] T007 Build `gateway/jarvis_gateway/protocol.py`: parse device `hello`/`listen`/`abort`/`mcp`
  text frames, build server `hello`/`stt`/`llm`/`tts`, enforce `session_id` on every outgoing
  message, ignore unknown types with one debug log, and raise a typed `ProtocolError` for malformed
  JSON or missing `type`. Contract tests in `gateway/tests/contract/test_protocol.py` load every
  T002 fixture and assert round-trip equality; malformed and unknown-type cases assert the
  documented behaviour.
- [X] T008 [P] `gateway/jarvis_gateway/jsonrpc.py`: request/response builders, monotonically
  increasing id per session, pending-future registry keyed by id, `-32601`/`-32000`/`-32602` error
  constructors, and a single-flight timeout wrapper. Unit tests in
  `gateway/tests/unit/test_jsonrpc.py` cover id matching, out-of-order responses, late responses
  after timeout (dropped, not raised), and duplicate ids.
- [X] T009 `gateway/jarvis_gateway/audio.py` codec half: PyAV-based Opus decoder (16 kHz mono) and
  encoder (24 kHz mono), with an import-and-round-trip smoke test that runs **on CT 914** in the
  project venv. `gateway/tests/unit/test_audio_codec.py` decodes the ten T003 packets to expected
  sample counts and proves `decode(encode(pcm))` returns the same number of samples with SNR above
  a documented threshold. If PyAV cannot be installed on this host, this task stops and escalates
  rather than silently switching codecs (plan risk 2).
- [X] T010 Audio framing and resampling half: `pcm16_16k_to_24k`, `frame_60ms` (1440 samples at
  24 kHz, 960 at 16 kHz), tail padding on the final frame, and byte-level PCM16 helpers. Tests in
  `gateway/tests/unit/test_audio_framing.py` assert exact frame sizes, no dropped samples, padded
  tail shorter than one frame, and that 24 kHz output has exactly 60 ms per packet. Also asserts the
  decoder accepts packets that our encoder produced at 16 kHz for the uplink fixture path.
- [X] T011 Spike and record the live brain contract in `docs/hermes-api-notes.md`: call
  `GET {JG_HERMES_BASE_URL}/v1/models` with the configured key, record the advertised model id, run
  one streamed chat completion with and without `X-Hermes-Session-Key`, and record whether the
  second turn sees the first turn's context (asserted by asking it to repeat a word). Capture the
  SSE chunk shape as `gateway/tests/fixtures/protocol/sse_chunk.txt`. Update the `JG_HERMES_*`
  defaults in `.env.example` to the observed values. No service code.
- [X] T012 [P] `gateway/jarvis_gateway/vad.py`: RMS energy VAD with hysteresis, speech-start after
  2 voiced frames, end after `JG_VAD_SILENCE_MS`, minimum speech, and a hard 30 s cap that reports
  `forced=True`. Tests in `gateway/tests/unit/test_vad.py` feed the T003 WAVs and assert: silence
  yields no utterance; a 100 ms burst yields `too_short`; normal speech yields start/end at the
  expected frame indices; the 35 s file forces end exactly at the cap; thresholds from config are
  honoured.
- [X] T013 [P] `gateway/jarvis_gateway/session.py`: `DeviceSession`, `SessionRegistry`
  (get-or-replace by device id, close-previous callback, idle scan, counters), state enum matching
  plan's state machine. Unit tests in `gateway/tests/unit/test_session.py`: two connects for one
  device replace the first exactly once; distinct devices coexist; idle scan closes only expired
  sessions; closing clears utterance/turn references; registry counts are correct.
- [X] T014 [P] `gateway/jarvis_gateway/chunker.py`: sentence segmentation for streaming TTS (split
  on `. ! ? …` and newline, flush at ≥20 chars, hard split at 220, flush remainder at end, no split
  inside decimal numbers or common abbreviations such as "Mr."). Unit tests in
  `gateway/tests/unit/test_chunker.py` with 15+ cases, including "Dr. Smith" and "3.5 seconds".
- [X] T015 `gateway/jarvis_gateway/ws_app.py` + app wiring in `gateway/jarvis_gateway/__main__.py`:
  aiohttp application factory with `GET /healthz`, `POST|GET /xiaozhi/ota/` (handler stub returning a
  fixed manifest until T031), and the WebSocket route. Implements handshake auth (constant-time
  compare of `Authorization: Bearer`), required headers, upgrade response, server `hello`, dispatch
  loop with close codes, and session registration. Contract tests in
  `gateway/tests/contract/test_handshake.py` using fake_device and the aiohttp test client: valid
  handshake gets hello with our session id and 24 kHz params; wrong/missing token gets 401 with no
  session created; missing `Protocol-Version`/`Device-Id`/`Client-Id` gets 400/401; protocol version
  2 gets 426; no hello within the timeout closes 1008; binary before hello closes 1003.
- [X] T016 [P] `gateway/jarvis_gateway/control.py`: UNIX-socket server (newline-delimited JSON,
  `tools/list` and `tools/call`, `-32000` not-connected), socket file lifecycle (create on start,
  remove on stop, 0660), and a client used by the bridge. Tests in
  `gateway/tests/unit/test_control.py`: round trip, malformed line, oversized line, client timeout,
  missing socket returns `-32000`, and permissions are 0660.

**Checkpoint**: `pytest gateway/tests/{unit,contract}` green; a client can handshake, get hello, and
be rejected cleanly when it misbehaves. No audio pipeline yet.

---

## Phase 3: User Story 1 — A spoken question, answered aloud (Priority: P1) 🎯 MVP

**Goal**: hello → listen → utterance → stt → llm → tts with audio, streaming, repeatable 20×.

**Independent Test**: `python -m tools.fake_device --wav tests/fixtures/audio/utterance_ok.wav
--expect-loop` against the running gateway with fake upstreams, or with real ones when `--live`.

### Tests for User Story 1 (write first, observe failing) ⚠️

- [ ] T017 [P] [US1] Contract test `gateway/tests/contract/test_turn_sequence.py`: drives the app
  over a real socket with fake_device and fake upstreams; asserts the ordered sequence hello →
  (listen start) → stt → llm(emotion in the firmware set) → tts start → sentence_start → ≥1 binary
  frame → tts stop, one session id throughout, and no message after `tts stop` until the next turn.
- [ ] T018 [P] [US1] Integration test `gateway/tests/integration/test_loop.py` (marker `live`): same
  journey against real whisper and the real Hermes server (fake edge-tts), asserting the transcript
  matches `utterance_ok.wav` and that follow-up turns keep context.
- [ ] T019 [P] [US1] Edge-case tests `gateway/tests/contract/test_empty_utterance.py`: silence and a
  100 ms noise burst produce no `stt`/`llm`/`tts`, no binary frames, and the session accepts the next
  turn; `listen stop` with no audio and a 30 s forced end behave the same way.

### Implementation for User Story 1

- [ ] T020 [US1] `gateway/jarvis_gateway/stt.py`: multipart WAV upload to
  `{JG_STT_URL}/v1/audio/transcriptions`, JSON `text` extraction, whitespace strip, timeout and
  non-2xx mapping to a typed error. Unit tests against fake_whisper: success, empty text, 500,
  timeout, oversized response.
- [ ] T021 [US1] `gateway/jarvis_gateway/brain.py`: streamed SSE chat completion with
  `X-Hermes-Session-Key: stackchan-<device-id>`, `Authorization` from config, model from config,
  incremental text callback, first-token timestamp, cancellation, and non-2xx/timeout handling. Unit
  tests against fake_hermes: chunk assembly across split SSE lines, `[DONE]`, error before and after
  first token, cancel mid-stream, and header assertion.
- [ ] T022 [US1] `gateway/jarvis_gateway/tts.py`: synthesise one sentence via edge-tts, decode MP3 to
  PCM, resample to 24 kHz, frame to 60 ms Opus, expose both a block iterator and a cancel hook. Unit
  tests against fake_edge_tts: correct frame count for a known sentence length, cancellation stops
  mid-sentence, per-sentence failure raises a typed error.
- [ ] T023 [US1] `gateway/jarvis_gateway/emotion.py`: map reply text and tool use to the firmware
  emotion set with a documented keyword table and a `neutral` default. Unit tests cover each
  emotion, unicode/emoji input, empty text, and that the function never returns a string outside the
  accepted set.
- [ ] T024 [US1] `gateway/jarvis_gateway/turn.py`: the orchestrator — utterance → stt message →
  brain stream → chunker → per-sentence `sentence_start` + frames → single `tts stop`; one turn at a
  time per session; T017 and T018 drive it. Emits `llm` with the chosen emotion before the first
  frame. On empty transcript, ends with no further messages (T019 green).
- [ ] T025 [US1] Failure paths in `turn.py`/`ws_app.py` per FR-018: STT timeout/error, brain non-2xx,
  brain stall past `JG_HERMES_TIMEOUT_S`, per-sentence TTS failure with the rest continuing, and
  all-sentences-failed. Each speaks `JG_APOLOGY_TEXT` when the user is waiting, ends with exactly one
  `tts stop` if speech began, and leaves the session usable. Contract tests in
  `gateway/tests/contract/test_failures.py` cover each branch and assert no unhandled task exception
  is logged.
- [ ] T026 [US1] Verify edge-tts on CT 914 inside the project venv: pin the version, confirm
  `en-GB-RyanNeural` synthesis works, measure first-audio latency for a 20-char and a 60-char
  sentence over five runs each, and record numbers plus the exact command in
  `docs/latency-baseline.md` (skeleton). If the voice or endpoint is unavailable, stop and escalate
  rather than switching voice silently.
- [ ] T027 [US1] Finish `gateway/tools/fake_device.py`: header construction, hello exchange,
  WAV→Opus 16 kHz framing with pacing, message collector with ordering assertions (`--expect-loop`),
  audio capture to `--out-dir`, `--ota` mode placeholder, exit codes, and
  `gateway/tests/unit/test_fake_device.py` for argument parsing and frame pacing. Then run the 20×
  loop and record the result in the task log (SC-004).
- [ ] T028 [US1] Wire per-turn timing records into `turn.py`/`logging.py`: `vad_end_ms`, `stt_ms`,
  `ttft_ms`, `first_audio_ms`, `tts_end_ms`, frame count, sentence count, outcome. Contract test
  asserts all fields present, monotonic, and absent for a turn that produced no speech.
- [ ] T029 [US1] `listen stop` handling: treat the device's stop as end-of-utterance in manual mode,
  close the utterance immediately, and ignore a later stop for the same utterance. Contract test with
  a client that sends stop 200 ms into speech.

**Checkpoint**: the synthetic device completes the full loop against real whisper and real Hermes;
SC-004 holds over 20 runs.

---

## Phase 4: User Story 2 — Bootstrap with no cloud account (Priority: P1)

**Goal**: the OTA manifest the firmware reads, with no activation and a correct clock.

### Tests for User Story 2

- [ ] T030 [P] [US2] Contract tests `gateway/tests/contract/test_ota.py`: POST the recorded
  system-info body → 200 JSON with exactly `firmware{version,url}`, `websocket{url,token,version}`,
  `server_time{timestamp,timezone_offset}`; no `activation`, no `mqtt`; `firmware.version` equals the
  body's `application.version` and falls back to `JG_FIRMWARE_VERSION` when the body omits it; GET
  returns the same shape; body absent/malformed still 200; wrong method → 405; `websocket.url`
  starts with `ws://` and contains the configured port and path.

### Implementation for User Story 2

- [ ] T031 [US2] `gateway/jarvis_gateway/ota.py`: manifest builder and handler replacing the T015
  stub, driven entirely by config and the request body (version from `application.version`, falling
  back to `JG_FIRMWARE_VERSION`), with `timestamp` in epoch milliseconds and `timezone_offset` in
  minutes (`-240` for America/New_York in October, derived from the host).
- [ ] T032 [US2] Clock contract tests `gateway/tests/contract/test_ota_time.py`: `timestamp` within
  1 s of `time.time()*1000`; value is milliseconds (rejects a seconds-shaped value by magnitude);
  offset matches the host's current UTC offset in minutes; a reminder scheduled from this clock fires
  at the right wall time in a simulated device timeline.
- [ ] T033 [US2] Cross-endpoint consistency test `gateway/tests/integration/test_ota_token.py`: take
  `websocket.token` from the OTA response and use it as the handshake bearer; assert success; then
  rotate the config value in-process and assert the old token fails and the new one succeeds
  (rotation without restart is not promised — the test restarts the app object).
- [ ] T034 [US2] `fake_device.py --ota`: fetch the manifest, assert the four blocks, then connect with
  the returned URL/token and complete a turn. Document in `gateway/README.md` that the firmware's
  `CONFIG_OTA_URL` must be `http://172.16.90.14:8010/xiaozhi/ota/` and `CONFIG_STACKCHAN_SERVER_URL`
  must be a dead address, matching plan §4.

**Checkpoint**: a `curl` and a synthetic device can bootstrap and talk; no activation key anywhere in
a response.

---

## Phase 5: User Story 3 — Interrupting the robot (Priority: P2)

**Goal**: abort and wake-word detection stop audio fast and leave the session usable.

### Tests for User Story 3

- [ ] T035 [P] [US3] Contract test `gateway/tests/contract/test_abort.py`: with fake TTS streaming a
  multi-sentence answer, send `abort{reason: wake_word_detected}` 500 ms in; assert no binary frame
  arrives more than 150 ms after the abort, exactly one `tts stop` follows, the turn task ends, and
  the session state is `IDLE`.
- [ ] T036 [P] [US3] Robustness test `gateway/tests/contract/test_abort_edges.py`: abort before any
  audio; abort twice; abort in `THINKING`; abort after the answer finished; abort with no turn. Each
  asserts no duplicate `tts stop`, no exception, no leaked task, and consistent state.

### Implementation for User Story 3

- [ ] T037 [US3] Cancellation plumbing in `turn.py` (`cancel: asyncio.Event`, checked between
  sentences and before each frame send), `tts.py` (stop the synthesis iterator), and `ws_app.py`
  (dispatch abort without awaiting the turn). Guarantee the "exactly one `tts stop` iff speech
  started" invariant with a single owner flag on the turn.
- [ ] T038 [US3] `listen state=detect` in `ws_app.py`: if a turn is speaking or thinking, trigger the
  same cancellation path; then start a fresh utterance. Contract test with a device that sends detect
  mid-speech and then streams a second question, asserting the second turn completes.
- [ ] T039 [US3] `fake_device.py --barge-in` scenario and integration test
  `gateway/tests/integration/test_barge_in.py` (20 iterations, timing percentile printed and recorded
  for SC-005).

**Checkpoint**: SC-005 measured; the plan's "wake word stops playback" behaviour exists before
hardware.

---

## Phase 6: User Story 4 — The brain moves the body (Priority: P2)

**Goal**: six tools, discovered once, forwarded with validation, never blocking a turn.

### Tests for User Story 4

- [ ] T040 [P] [US4] Contract tests `gateway/tests/contract/test_mcp_session.py` for
  `gateway/jarvis_gateway/mcp_client.py`: initialize after a `features.mcp` hello; `tools/list`
  including a paginated second page; `tools/call` id matching with two concurrent calls answered out
  of order; 5 s timeout failing one call without touching the other; a device error response passed
  through unchanged; a device that never answers `initialize` leaving the audio path usable.
- [ ] T041 [P] [US4] Schema fidelity test `gateway/tests/contract/test_bridge_schemas.py`: the six
  tool descriptors in `bridge.py` match the names, argument names, types and limits recorded from
  `hal_mcp.cpp` (fixture `gateway/tests/fixtures/protocol/tool_schemas.json`, transcribed with file
  and line references in the fixture's `_source` key). Any drift fails the build.
- [ ] T042 [P] [US4] Validation tests `gateway/tests/unit/test_tool_validation.py`: yaw outside
  −128…128, pitch outside 0…90, speed outside 100…1000, RGB outside 0…168, duration outside
  1…86400, non-bool `repeat`, missing `id`, unknown extra arguments; each rejected with a message
  naming the field and the allowed range, and nothing forwarded.
- [ ] T043 [P] [US4] Lifecycle tests `gateway/tests/integration/test_tool_lifecycle.py`: device
  reconnect refreshes the tool list; an in-flight call whose device disconnects resolves as an error
  rather than hanging; a call with no session returns "robot not connected" in <50 ms (SC-009).

### Implementation for User Story 4

- [ ] T044 [US4] `gateway/jarvis_gateway/mcp_client.py`: gateway-as-MCP-client over the live socket
  (initialize, tools/list with pagination, tools/call with id matching and 5 s deadline), built on
  T008; then wire it into `ws_app.py` so a `features.mcp` hello starts discovery, stores descriptors
  on the session, logs the list once, and refreshes on reconnect — off the audio critical path.
- [ ] T045 [US4] `gateway/jarvis_gateway/bridge.py` stdio server: newline-delimited JSON-RPC
  `initialize` (protocolVersion `2024-11-05`, serverInfo `jarvis-robot`), `tools/list`, `tools/call`;
  allowlist enforced; unknown tool → `-32601`; a `--dry-run` mode that lists tools without a gateway.
  Unit tests drive it over `asyncio` streams with a fake control client.
- [ ] T046 [US4] Bridge forwarding over the T016 control socket with a 200 ms connect timeout and the
  `-32000` not-connected path; no retry loops. Contract test against the real gateway process with and
  without a connected device.
- [ ] T047 [US4] Argument validation in `bridge.py` from a single declarative table shared with the
  schema test (T041), so the advertised schema and the enforced rules cannot diverge.
- [ ] T048 [US4] Reconnect and stale-call handling in `session.py`/`control.py` to satisfy T043: fail
  pending futures on disconnect with a typed error, clear caches, and make the next call get "robot
  not connected".
- [ ] T049 [US4] `docs/gateway-runbook.md`: the Hermes `mcp_servers` entry that launches the bridge
  (`command`, `args`, working directory, no secret), the six tool names as Hermes sees them, how to
  check the robot is connected, and how to rotate the token. A test in
  `gateway/tests/unit/test_runbook_snippet.py` runs the documented command from the repo root and
  asserts `--dry-run` lists the six tools (the doc cannot drift).

**Checkpoint**: all six tools work end-to-end against a synthetic device; a dead robot degrades to
one sentence and the turn continues.

---

## Phase 7: User Story 5 — Running it without surprises (Priority: P3)

**Goal**: auth that holds, health that means something, timings that add up, logs that stay clean.

- [ ] T050 [P] [US5] `/healthz` fields (version, uptime seconds, active sessions, last turn timings
  summary) plus contract test in `gateway/tests/contract/test_health.py`, including behaviour before
  any session has ever run.
- [ ] T051 [P] [US5] Auth hardening tests in `gateway/tests/contract/test_auth.py`: missing header,
  empty token, wrong token, `Basic` scheme, token with trailing whitespace, token of wrong length,
  duplicate `Authorization` headers; 20 iterations each, all rejected, zero sessions created; unit
  test asserts `hmac.compare_digest` is used (monkeypatch spy) and that the token never appears in
  logs.
- [ ] T052 [US5] `gateway/systemd/jarvis-gateway.service` and `gateway/systemd/env.example`:
  `User=hermes`, `EnvironmentFile=/etc/jarvis-gateway/env` (0600), `RuntimeDirectory=jarvis-gateway`
  with `RuntimeDirectoryMode=0750`, `Restart=on-failure`, `MemoryMax=512M`, `NoNewPrivileges=yes`,
  `PrivateTmp=yes`, `ProtectSystem=full`. Validate with `systemd-analyze verify` where available;
  otherwise a unit test parses the file and asserts the required keys.
- [ ] T053 [US5] Logging hygiene: canary test that runs a turn whose transcript and reply contain
  unique strings, captures logs, and asserts neither string nor the token appears with
  `JG_LOG_TRANSCRIPTS=false`; asserts they do appear when enabled; asserts the apology text is logged
  as a reason code, not verbatim.
- [ ] T054 [US5] Outbound-connection assertion: instrument the aiohttp client session at start-up to
  record every host:port dialled, and assert at the end of a full turn that the set is exactly
  {whisper host, Hermes host, speech endpoint} with no vendor host (SC-006). Test in
  `gateway/tests/integration/test_egress.py` using fake upstreams for determinism, plus a `live`
  variant.
- [ ] T055 [US5] Idle timeout and soak: `gateway/tests/integration/test_soak.py` (marker `slow`) keeps
  the service up for an hour with one idle session, samples RSS from `/proc/self/statm`, asserts
  growth <20 MB, `/healthz` still 200, and no task warnings emitted (SC-008).

**Checkpoint**: the service can be handed to the operator: it refuses strangers, reports health,
survives idle, and leaks nothing spoken.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: everything that makes the next worker's life possible and closes the phase.

- [ ] T056 [P] `gateway/README.md`: install (venv, `pip install -e .[dev]`), run, test (including
  `-m "not slow and not live"`), env file, control socket, bridge registration pointer, and links to
  `specs/001-jarvis-gateway/{spec,plan,tasks}.md` and `docs/gateway-runbook.md`.
- [ ] T057 [US1] Complete `docs/latency-baseline.md`: per-stage medians and p90 over ≥20 real turns
  (real whisper, real Hermes, real edge-tts), the TTFT measured with `curl` plus timestamps as plan
  §6 item 4 asks, the date, host, and the exact commands. Include the delta against plan §7 targets
  and a one-line verdict for SC-001…SC-003.
- [ ] T058 [P] Security review checklist in `docs/gateway-runbook.md`: confirm `ss -ltnp` shows only
  `:8010` from this service; confirm the bridge has no TCP listener; token rotation; allowlist review;
  what to do if the robot is stolen or the token leaks (rotate in both files, reconnect).
- [ ] T059 Run the quickstart end-to-end on CT 914: fresh venv, `.env` from the systemd env file, real
  whisper, real Hermes, real edge-tts, synthetic device, then `--ota` and `--barge-in` modes. Record
  command output and numbers in the task log; fix only what this feature owns.
- [ ] T060 Cross-check the firmware overlay contract against gateway config: assert
  `JG_OTA_PATH`/`JG_WS_PATH`/`JG_BIND_PORT` match the URLs the plan's `sdkconfig.defaults.local` will
  carry, and add a row to `docs/gateway-runbook.md` stating that changing either side requires
  changing both. Unit test asserts the paths begin with `/xiaozhi/` and that the manifest URL is built
  from config rather than hard-coded.
- [ ] T061 Independent review by a different model lineage (plan §6 item 5, §12): attach the diff and
  this task list, record findings in `docs/gateway-runbook.md` under "Review log", and open follow-up
  cards for anything out of scope rather than widening this work.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on T001–T006; T015 blocks every user story. T009/T010 block
  T022; T007 blocks T015; T008 blocks T040/T045.
- **US1 (Phase 3)**: depends on Foundational. Produces the loop everything else rides on.
- **US2 (Phase 4)**: depends on Foundational only; can run in parallel with US1 by a second worker
  because it touches `ota.py` and its tests, not the turn path.
- **US3 (Phase 5)**: depends on US1 (there must be speech to abort).
- **US4 (Phase 6)**: depends on Foundational (T008, T015, T016); independently testable with a
  synthetic device; does not need US1's audio pipeline.
- **US5 (Phase 7)**: T050/T051/T054 after the US1 handshake and turn; T052 anytime after T004; T055
  last.
- **Polish (Phase 8)**: after the stories it references; T059 is the gate before declaring Phase 0
  done.

### Within each story

Tests first, observed failing (T017/T018/T019, T030, T035/T036, T040–T043, T050/T051 all precede
their implementation tasks). Codec before framing, protocol before dispatch, session before turn,
discovery before forwarding.

### Parallel opportunities

- T002–T006 are all [P] and touch separate files; they can be split across workers.
- T008, T012, T013, T014 are [P] within Foundational.
- US1's T017/T018/T019 (tests) can be written together; T020/T021/T022/T023 touch separate modules
  and can run in parallel once their fakes are stable.
- US4's T040–T043 are [P] tests, then T044/T045/T046/T047/T048 are nearly disjoint files
  (`mcp_client`/`ws_app` vs `bridge` vs `session`/`control`).
- US2 can be run by a second worker in parallel with US1; both depend only on T015.

### Suggested MVP

Stop after Phase 4. That is hello → stt → llm → tts with audio, plus a bootstrap the robot can
actually use. US3–US5 are needed before the robot lives in the house, not before the first flash.

---

## Notes for whoever picks this up

- The repo is **not** a git checkout and has no `.specify/`; a follow-up card covers scaffolding.
  Until then, every path above is created new and `git`-related steps do not apply.
- Do not edit the firmware tree or `.research_stackchan/` — those are read-only references here.
- If a task cannot be completed as written because a named host or service is missing, block with
  the exact command and output; do not substitute a different service (especially not a hosted
  speech or transcription API).
- Tick only the tasks you actually completed; one writer per line.
