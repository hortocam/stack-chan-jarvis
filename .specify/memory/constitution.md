# Jarvis Device Gateway Constitution

**Version**: 1.0.0 | **Ratified**: 2026-10-30 | **Last amended**: 2026-10-30

This constitution adds no rule that the project's own documents do not already state. It turns
the gates in `specs/001-jarvis-gateway/plan.md` → "Constitution Check" (G1–G6) into standing gates
for every feature in this repository, and its sources are:

- `docs/stackchan-jarvis-plan.md` (v2) §2 (non-goals), §4 (firmware: no patches), §5 (gateway
  design), §9 (security), §10 decision 5 (governed SDD).
- `specs/001-jarvis-gateway/plan.md` → "Constitution Check", gates G1–G6.
- `specs/001-jarvis-gateway/spec.md` → "Out of Scope".

Where this file and those documents disagree, the documents win and this file is corrected.

## Core Principles

### I. Loopback Brain, One Exposed Port

The gateway MUST be the only new network listener this project adds. It MUST bind `:8010` on
CT 914 (`172.16.90.14`) and serve robot sessions from VLAN 20; the Hermes brain MUST be reached at
a loopback address (`127.0.0.1` — the port is configuration, not a constant); and the control
channel between the gateway and the Hermes-launched MCP bridge MUST be a UNIX socket or inherited
stdio, never a TCP port. The design MUST NOT add a WAN port forward; if off-LAN access is ever
wanted it goes behind the existing Cloudflare Tunnel + Access (plan §9).

**Rationale**: plan §9 — robot on VLAN 20, gateway and Hermes on VLAN 90; only `:8010` is exposed
and the brain is loopback-only. Plan gate G2.

**Check**: `ss -ltnp` on CT 914 shows the new listener on `:8010` and nowhere else; the configured
brain base URL parses to `127.0.0.1`/`localhost`; no new port appears in the bridge/control path.

### II. Protocol Fidelity to xiaozhi v2.2.4

The gateway MUST implement only the message types and binary framing documented for
`78/xiaozhi-esp32` v2.2.4 (binary protocol version 1): the JSON `hello`/`listen`/`abort`/`mcp`
device→server set and the `hello`/`stt`/`llm`/`tts`/`mcp` server→device set, with 60 ms Opus
frames (16 kHz uplink, 24 kHz downlink). The firmware MUST NOT be patched; `.research_stackchan/`
is read-only reference and a change there is out of scope. Where the vendor sources are
ambiguous, the behaviour MUST be recorded as an open discrepancy (plan.md → "Discrepancies and
Open Questions"), never invented into the spec.

**Rationale**: plan §4 and gate G4 — the firmware is M5Stack's, pinned at v2.2.4; its two URL
knobs plus the shared token are the only coupling points we own.

**Check**: every message type in `gateway/jarvis_gateway/protocol.py` maps to a fixture
transcribed from `.research_stackchan/xiaozhi-esp32` at tag `v2.2.4`; no vendor source file is
modified.

### III. No New Infrastructure, No Cloud Account

Deployment MUST be one systemd unit on the existing CT 914, with Python dependencies in a project
virtualenv. No Docker, no new container or CT, no new VLAN or firewall rule, and no cloud/vendor
account or API key MAY be introduced. edge-tts is permitted because it needs no account, key or
vendor integration; whisper and Hermes are consumed exactly as they already run.

**Rationale**: plan §2 non-goals and §5 "what we deliberately do not build"; gate G1.

**Check**: the repository contains no Dockerfile or compose file; `gateway/systemd/` is the only
deployment artifact; every endpoint named in `gateway/.env.example` and the code is whisper, the
loopback Hermes server, or the Microsoft speech endpoint edge-tts already uses.

### IV. One Shared Token, Six Tools, Never Block the Brain

The OTA/WebSocket handshake MUST authenticate with the single shared bearer token, compared in
constant time; the token MUST never be logged, echoed in errors, or committed. The MCP bridge MUST
expose exactly the robot's six documented body tools and MUST refuse any other tool name. When the
robot is absent or idle, a tool call MUST return "robot not connected" promptly — it MUST NOT
block, hang, or fail a Hermes turn.

**Rationale**: plan §5 (body control; six tools) and §9 (one shared token; the robot is an open
microphone into the agent); gate G3.

**Check**: contract tests cover a wrong token (rejected), an unlisted tool name (refused), and a
tool call with no live session (fast error); a scan of logs and fixtures finds no token value.

### V. Provable Without Hardware

Every behaviour MUST be demonstrable with no robot attached: a synthetic device client plus fake
whisper/Hermes/edge-tts upstreams MUST cover handshake, a full turn, abort and body tools
headlessly. Tests MUST be written before the code that satisfies them, and a test MUST have been
observed failing for the right reason before its task is called done. Tests that need the real
services MUST sit behind an explicit marker and be skipped by default.

**Rationale**: plan §6 item 3 and gate G5; spec FR-017; hardware does not arrive until
2026-11-05, so hardware-free proof is the only proof available.

**Check**: `pytest` passes with no hardware and no network; each task's diff includes a test that
fails when the implementation is wrong; live-service tests are marked and deselected by default.

### VI. Stay Inside the Feature Boundary

Work MUST stay inside the boundaries the spec sets: no firmware build or flash, no RPi4 front-end
change, no StackChan app / vendor Go server / vendor cloud / `stackChan/ws` avatar channel, no LVGL
face skin, no persona or SOUL work, no tool-policy change beyond the six-tool allowlist, no
multi-robot support, WAN access, per-user authentication or telemetry. When a task appears to need
any of these, the correct response is to stop and report the exact rule and conflicting line — not
to widen the scope.

**Rationale**: spec.md → "Out of Scope"; plan gate G6; stackchan plan §2 non-goals.

**Check**: a card's diff stays within the files its task names; any `gateway/` change that would
require firmware, face, persona or infrastructure work is reported as a conflict, not carried out.

## Additional Constraints

- **Python 3.11 only** (`~/.local/bin/python3.11`); the host's system 3.13 interpreter is not used.
- **One process, in-memory state**, at most two concurrent device sessions; 256 MB RSS ceiling.
- **Latency budget**: last voiced frame → first audible word < 3.0 s median / < 4.0 s p90;
  body-tool round trip < 300 ms; abort → silence < 150 ms. These are acceptance numbers, not
  aspirations.
- **Open microphone**: the robot is an unauthenticated local voice interface; destructive
  operations stay behind a spoken confirmation in SOUL (plan §9). This feature does not implement
  that policy and MUST NOT remove or bypass it.

## Development Workflow

- **Spec-first, governed SDD** (plan §10 decision 5): specify → plan → tasks → implement; a worker
  implements only the task IDs its card names, a different lineage reviews, and the owner merges.
- **Tests before code** for every task; the test is observed failing for the right reason first.
- **Constitution Check is a gate, not a formality**: every feature's `plan.md` MUST carry a
  "Constitution Check" table mapping each principle to PASS/FAIL with evidence. A FAIL blocks
  implementation until it is resolved or the plan records a justified waiver.

## Governance

- This constitution supersedes other practice for spec-driven work in this repository, but it
  derives from `docs/stackchan-jarvis-plan.md` (v2), `specs/001-jarvis-gateway/plan.md` and
  `specs/001-jarvis-gateway/spec.md`. If a principle here contradicts those documents, the
  documents win and this file MUST be corrected.
- Gate mapping: G1 → Principle III, G2 → I, G3 → IV, G4 → II, G5 → V, G6 → VI.
- Amendments require a version bump, a recorded rationale, and a re-run of the affected feature's
  Constitution Check. Versioning: MAJOR for a removed or redefined principle, MINOR for an added
  principle or a materially stricter gate, PATCH for wording only.
- A gate is waived only by an explicit, recorded owner decision in the feature's plan
  ("Complexity Tracking"); nothing in this file grants a waiver by default.
