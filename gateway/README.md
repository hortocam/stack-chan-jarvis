# Jarvis Device Gateway

The xiaozhi-protocol service that gives the M5Stack StackChan robot the Jarvis
brain running on CT 914. Python 3.11, one process, in-memory state.

Phase 3 (US1, T017–T029) is wired end to end: `hello → listen → utterance →
stt → llm → tts` with 24 kHz Opus audio, streaming sentence by sentence.

## Install

```bash
cd gateway
~/.local/bin/python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Configure

Every setting comes from the environment with a `JG_` prefix; `.env.example`
lists them all with non-secret defaults. `JG_DEVICE_TOKEN` and
`JG_HERMES_API_KEY` are required and have no defaults. On CT 914 the real file
is `/etc/jarvis-gateway/env` (mode 0600), read by the systemd unit.

## Run

```bash
.venv/bin/python -m jarvis_gateway        # binds :8010, the only new listener
```

## Test

```bash
cd gateway
.venv/bin/pytest                      # live/slow markers are deselected by default
.venv/bin/pytest -m live              # needs JG_HERMES_API_KEY and real whisper
.venv/bin/pytest -m slow              # the 20-turn loop (SC-004)
.venv/bin/ruff check .
```

## Synthetic device

`tools/fake_device.py` plays the robot over a real socket — the same client the
contract tests drive. It speaks a 16 kHz mono PCM16 WAV as 60 ms Opus frames,
one packet per binary frame, and can assert the whole turn sequence:

```bash
.venv/bin/python -m tools.fake_device \
    --url ws://127.0.0.1:8010/xiaozhi/v1/ --token "$JG_DEVICE_TOKEN" \
    --wav tests/fixtures/audio/utterance_speech.wav \
    --expect-loop --out-dir /tmp/turn-audio
```

`--expect-loop` asserts `stt → llm (firmware emotion) → tts start →
sentence_start → binary frames → exactly one tts stop`, one session id
throughout, and silence after the stop. Exit codes: 0 asserted sequence,
1 contract violation, 2 usage error. `--ota` fetches the manifest at `--url`
(placeholder until T034).

## Layout

- `jarvis_gateway/` — the service: `config`, `logging`, `protocol`, `audio`,
  `vad`, `session`, `stt`, `brain`, `tts`, `emotion`, `turn`, `ws_app`.
- `tools/fake_device.py` — the synthetic robot.
- `tests/unit/` — unit tests; `tests/contract/` — wire contracts over a real
  socket; `tests/integration/` — the live loop and the 20-turn run;
  `tests/fakes/` — fake whisper/Hermes/edge-tts; `tests/fixtures/protocol/` —
  wire messages transcribed from xiaozhi v2.2.4; `tests/fixtures/audio/` —
  generated audio fixtures (`make_audio.py`).

Measured latency: `../docs/latency-baseline.md`. Source of truth:
`../specs/001-jarvis-gateway/{spec,plan,tasks}.md` and
`../docs/stackchan-jarvis-plan.md`. The robot is a device, not a trust
boundary: everything it sends is untrusted input.
