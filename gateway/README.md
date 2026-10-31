# Jarvis Device Gateway

The xiaozhi-protocol service that gives the M5Stack StackChan robot the Jarvis
brain running on CT 914. Python 3.11, one process, in-memory state.

This is the Phase 1 stub: the package, the configuration loader, JSON-lines
logging, the fake upstreams and the protocol/audio fixtures exist; the service
itself is not wired yet (`python -m jarvis_gateway` starts nothing until T015).

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

## Test

```bash
cd gateway
.venv/bin/pytest                      # live/slow markers are deselected by default
.venv/bin/ruff check .
```

## Layout

- `jarvis_gateway/` — the service package (`config`, `logging` today).
- `tests/unit/` — unit tests; `tests/fakes/` — fake whisper/Hermes/edge-tts;
  `tests/fixtures/protocol/` — wire messages transcribed from xiaozhi v2.2.4;
  `tests/fixtures/audio/` — generated audio fixtures (`make_audio.py`).

Source of truth: `../specs/001-jarvis-gateway/{spec,plan,tasks}.md` and
`../docs/stackchan-jarvis-plan.md`. The robot is a device, not a trust
boundary: everything it sends is untrusted input.
