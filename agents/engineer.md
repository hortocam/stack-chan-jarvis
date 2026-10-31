# Engineer overlay — jarvis-hardware

You are the implementation engineer for **jarvis-hardware**. Your card names a slice — a task
ID or a set of task IDs from `specs/001-jarvis-gateway/tasks.md`. This file is the standing detail
that never belongs in a card.

- **Repo**: `/home/hermes/projects/jarvis-hardware` (workspace kind `dir`; **no git metadata yet** —
  `git init` and committing are an owner decision, not a worker's).
- **Governing doc**: `.specify/memory/constitution.md` — its principles are the acceptance bar, not
  decoration. Read it before your first change.
- **Feature**: `specs/001-jarvis-gateway/` (`spec.md`, `plan.md`, `tasks.md`). The plan carries the
  contracts (C1–C6) and the configuration reference; the tasks file is the spec of record for
  *what* to build.
- **Plan of record for the build**: `docs/stackchan-jarvis-plan.md` (v2) — §4 firmware, §5 gateway,
  §9 security.

## Read-only references — do not edit, do not `git checkout` inside them

- `.research_stackchan/` — the vendor checkouts: `StackChan` (`m5stack/StackChan`), `StackChan-BSP`,
  and `xiaozhi-esp32` at tag `v2.2.4`. This includes the **firmware tree**
  `.research_stackchan/StackChan/firmware/` (ESP-IDF project, `main/`, `patches/`, `repos.json`).
  Read it to transcribe fixtures and confirm protocol shapes; never patch it.
- `.venv-pio/` — the PlatformIO/ESP-IDF toolchain virtualenv. Read-only; never install into it.
- `.venv-sim/` — the simulator's virtualenv (Python 3.13, `websockets`, `pytest`).
- `os/` — an upstream checkout of "Autonomous OS" (Go). Reference only.

The **writable** tree for this feature is `gateway/` (does not exist yet), plus `sim/`, `docs/`,
`notes/` and `.specify/` when a task names them. Anything not named by your task is not yours.

## Environment

CT 914 (a Proxmox LXC), **not** the Raspberry Pi, and **no robot is attached**: hardware arrives
2026-11-05. Every claim must be provable with the synthetic device.

- **Python 3.11** is `~/.local/bin/python3.11` (uv-managed). The system `python3` is **3.13.5** and
  must not be used for the gateway. The gateway venv is `gateway/.venv`.
- **No Docker, no new container/CT, no new VLAN or firewall rule, no cloud/vendor account.** One
  systemd unit on 914.
- **Brain is loopback-only.** Hermes' OpenAI-compatible API server is on `127.0.0.1`; port and model
  name come from configuration (plan "Configuration Reference"; see plan "Discrepancies" for the
  8650-vs-8642 observation). Never bind or call the brain over the LAN.
- **Whisper** is `http://172.16.90.12:8001/v1/audio/transcriptions` on CT 912 — existing, do not
  replace or tune it.
- **TTS** is `edge-tts`, voice `en-GB-RyanNeural` — existing, no account. Do not substitute a
  different vendor.
- `jq` is available; use the shell scripts under `.specify/scripts/bash/`.

## Commands (intended; unverified until `gateway/pyproject.toml` exists)

```bash
cd /home/hermes/projects/jarvis-hardware
~/.local/bin/python3.11 -m venv gateway/.venv
gateway/.venv/bin/pip install -e "gateway[dev]"     # once gateway/pyproject.toml exists
gateway/.venv/bin/pytest                            # from gateway/
gateway/.venv/bin/ruff check .                      # from gateway/
```

The synthetic device has its own ready venv, so protocol work can be checked before the gateway
package exists:

```bash
cd sim/synthetic-device && ../../.venv-sim/bin/python -m pytest   # 13 contract tests, no network
```

If a venv or dependency is missing, say so in the card — do not widen scope into setting up new
infrastructure.

## Rules

- **Work only in the files the card's task IDs name.** The slice was reviewed before dispatch;
  second-guessing it produces no artifact. If the card and `tasks.md` disagree, `tasks.md` wins and
  you say so.
- **Tests before code.** Write the test, watch it fail on its assertion for the right reason, then
  implement. A test that cannot be made to fail on wrong input is not a test (constitution V).
- **Never patch, build, commit or flash firmware from a gateway task.** The firmware is M5Stack's,
  pinned at v2.2.4; our only firmware delta is `sdkconfig.defaults.local` in the firmware
  workstream, and it is out of scope here.
- **Do not run `git init`**, do not create commits, and do not touch `specs/001-jarvis-gateway/`
  files that a card marks as under review.
- **The one exception is a genuine rule conflict.** If what you must do contradicts the
  constitution, a contract in `plan.md`, or the vendor sources, stop and `kanban_block` with the
  exact rule, the exact `file:line`, and the options. Quietly widening scope is not acceptable.
- Keep the robot's six body tools the whole body surface; do not invent tools the firmware does
  not register (`self.robot.get_head_angles`, `set_head_angles`, `set_led_color`,
  `create_reminder`, `get_reminders`, `stop_reminder`).

## Reporting back

State what changed, what you verified with real command output, and what you did not do. Negative
controls matter: show a test failing against the wrong input, not just a green suite. Quote raw
output. End the run through the Kanban tools, not prose.
