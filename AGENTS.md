# StackChan Jarvis — repo rules

This repo holds the **Jarvis Device Gateway**: the xiaozhi-protocol service that gives the
M5Stack StackChan robot the Jarvis brain running on CT 914. It does not contain firmware.

## Read these first, in this order

1. `docs/stackchan-jarvis-plan.md` — the plan of record (v2): architecture, phases, decisions.
2. `specs/001-jarvis-gateway/{spec,plan,tasks}.md` — the phase you are working, its contracts.
3. `agents/engineer.md` — role overlay: what you may touch, what is read-only.

## Hard rules for this repo

1. **No new infrastructure.** No Docker, no new container, no cloud or vendor account.
   Everything runs on CT 914 or uses services that already exist.
2. **The brain is the Hermes API server**, OpenAI-compatible, at
   `http://127.0.0.1:8642/v1` (same host). Read `API_SERVER_KEY` from the environment —
   never hardcode it, never log it, never commit it.
3. **Speech services are consumed as-is.** STT is the existing whisper at
   `POST http://172.16.90.12:8001/v1/audio/transcriptions` (multipart `file`, optional
   `model`/`language`/`response_format`) → `{"text": "..."}`. TTS is edge-tts
   `en-GB-RyanNeural`. Do not replace, retrain or "improve" either one here.
4. **Protocol fidelity.** The wire protocol is pinned to `78/xiaozhi-esp32` **v2.2.4**
   (`docs/websocket.md`, `docs/mcp-protocol.md`). Implement what the spec says; invent no
   message types, no extra fields, no renamed JSON keys.
5. **Tests before code, and a test must be able to fail.** Every behaviour lands with a test
   that has been seen red on wrong input (negative control). A green suite with assertions
   that cannot fail is a defect.
6. **Never commit secrets, firmware binaries, or vendor checkouts.** The firmware tree
   (`.research_stackchan/**`, any `firmware/**`) is a read-only reference; so is `.venv*`.
7. **Do not flash, patch or build firmware from a gateway task.** The only firmware delta is
   `sdkconfig.defaults.local`, owned by a separate card.
8. **Stay in scope.** Work only the task IDs named on your card, on a branch cut from
   `main`; the coordinator is the sole merge authority.

## Commands

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
```

Unverified until `gateway/pyproject.toml` exists — the first setup task creates it. Do not
treat the block above as evidence that these commands work.

## Network posture

- Gateway listens on `:8010`, reachable from VLAN 20 only. No WAN exposure, ever.
- The robot is a device, not a trust boundary: everything it says is untrusted input.
