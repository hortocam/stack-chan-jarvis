# stack-chan-jarvis

Jarvis Device Gateway — the service that lets an M5Stack StackChan robot talk to the Jarvis
backend on CT 914: xiaozhi-protocol WebSocket server, speech in/out, and an MCP bridge that
lets the agent move the robot's head, lights and reminders.

- **Plan of record:** `docs/stackchan-jarvis-plan.md`
- **Feature spec / plan / tasks:** `specs/001-jarvis-gateway/`
- **Repo rules (read before changing anything):** `AGENTS.md`
- **Role overlay:** `agents/engineer.md`

Nothing here runs in the cloud. There is no vendor account, no Docker, and no new
infrastructure: the gateway is a single Python service on CT 914, using the whisper service
on CT 912 and edge-tts for voice.

## Status

Phase 0 — spec complete, implementation not started.

## Running the tests

Use `make` from the repo root:

```bash
make install   # creates gateway/.venv and installs dev deps
make test      # pytest, inside gateway/
make lint      # ruff, inside gateway/
```

The working directory matters: pytest picks up `asyncio_mode = "auto"` from
`gateway/pyproject.toml`. Run pytest from the repo root and it never reads that file, so the
async fixtures fail with "async def functions are not natively supported" — which reads like
a missing plugin and is not one. `-p pytest_asyncio` does not fix it either; it loads the
plugin but leaves the mode unset.
