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
