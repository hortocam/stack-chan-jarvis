# Brain latency options (P3.2)

**Measured**: 2026-10-31 14:44–15:20 EDT (18:44–19:20 UTC) on CT 914 (`172.16.90.14`),
no robot attached, load average **1.08–1.71** on 8 cores (sampled before, during and
after the runs). Real whisper on CT 912, the live Hermes API server on loopback
`:8642`, real `en-GB-RyanNeural` edge-tts, the live default profile (`Jarvis`) served
by the running multiplexed gateway.

This card asks whether a lean device profile beats the default channel's 7.1 s TTFT.
It measured the default channel end to end, and measured what the 7 s is made of.
**The lean arm itself was not measured**: it needs a second Hermes profile, and that is
an owner decision that is still open on the board (t_12360673, options A/B/C). The
numbers below are what can be said without making that decision.

## The three variants

| Variant | TTFT p50 | TTFT p90 | Answer-audible p50 | Answer-audible p90 | Prompt tokens per turn | What the robot would lose |
|---|---|---|---|---|---|---|
| **1. Default profile (measured, 20 turns)** | **8.41 s** | **20.16 s** | **9.52 s** | **21.00 s** | 16,707 static; **53.7 k–79.5 k** on a turn where the brain uses a tool | nothing |
| 2. Lean `jarvis-body`, same model | **not measured** — owner decision open (t_12360673) | — | — | — | ~0.5–1 k expected, **not measured** | shared memory (`MEMORY.md` 2,215 B + `USER.md` 1,378 B), the 149-entry skills catalogue, and the full 57-tool api_server surface (web, browser, terminal, files, code execution, vision, image gen, skills, todo, memory, session search, delegation, cron, connections, Home Assistant, kanban, …) |
| 3. Lean + a faster model | **not measured** — same gate, and no faster model exists on the provider (see below) | — | — | — | — | everything in 2, plus model quality |

"TTFT" is the gateway's own `ttft_ms`: from handing the transcript to the brain to
the first `delta.content`. "Answer-audible" is the device-side time from `listen stop`
to the first audio frame of the answer (the acknowledgement frame is excluded). The
row-1 numbers come from `gateway/tools/latency_bench.py --turns 20`, one fresh device
scope (`bench-p32-baseline`), the standard `utterance_speech.wav`; 20/20 turns spoken,
no failures, acknowledgement played 20/20, first audible word 1.22 s p50 / 1.33 s p90.

Raw per-turn TTFT (s): 4.65, 5.68, 5.77, 6.81, 7.18, 7.65, 7.83, 8.20, 8.31, 8.37,
8.45, 8.70, 10.06, 11.25, 13.30, 17.60, 17.88, 19.96, 21.88, 24.69. The spread is the
brain's, not the gateway's: whisper was 1.22 s p50 / 1.34 s p90 across the same turns.

## What the 7–8 s is made of

Three probes against the same live channel, same model, same 16.7 k-token prompt:

| Probe | n | First `reasoning_content` | First `content` | `prompt_tokens` |
|---|---|---|---|---|
| `Reply with exactly: OK` | 10 | — | **639–918 ms, p50 712 ms** | 16,707 (constant) |
| `In one sentence, what is the capital of France?` | 5 | — | **590–786 ms** | 16,712 |
| `Jarvis, what is the weather like today?` (the bench utterance) | 3 | 636–664 ms | **4,212 / 6,110 / 6,395 ms** | **53,691 / 63,577 / 79,508** |

Reading:

1. **Prefill of the 16.7 k-token prompt costs ~0.65 s**, not 7 s. The model starts
   emitting at ~640 ms on every probe, including the weather one.
2. The weather turn then spends **4–6 s before the first content token** and its
   `prompt_tokens` are 3–5× the static prompt — the extra tokens are tool results. The
   answers contain live conditions ("51°F, humidity 37%, wind ~8 mph"), which only the
   `web` toolset can supply, so the api_server agent is doing a web search inside the
   turn before it answers.
3. The bench's 8.4 s p50 is that same tool loop on a session whose context is growing
   (turns 1–12 sit at 4.6–8.7 s; the last four at 17.9–24.7 s), under load 1.1–1.7.

So on this host the prompt is **at most ~0.65 s** of the 8.4 s. A profile that cut the
prompt to ~1 k tokens could not save more than that from prefill alone; what a lean
profile would actually remove is the **tool loop and the skills/memory that invite it**
— a capability change, not a prompt-size tune. That is the number the owner decision
turns on, and it is not measured here.

## No faster model is available on the same provider

The provider is `ollama.com` (`model.provider: ollama-cloud`). The request path honours a
per-request `model` + `provider` (the API server echoes the model it ran), so the
provider's catalogue can be compared read-only, with no profile and no config change —
same 16.2–16.7 k prompt, same "capital of France" turn:

| Model | n | TTFT | vs current |
|---|---|---|---|
| **deepseek-v4.1-flash** (current) | 3 | **621 / 671 / 717 ms** | — |
| glm-5.3-flash | 3 | 1,510 / 1,673 / 2,037 ms | +0.9–1.3 s |
| minimax-m3.1-flash | 3 | 1,311 / 1,612 / 2,580 ms | +0.7–1.9 s |
| gpt-oss:20b | 2 | 1,695 / 1,888 ms | +1.1 s |
| nemotron-3-nano:30b | 2 | 1,857 / 2,270 ms | +1.2–1.6 s |

Every other "flash"-tier model in the catalogue is **2–4× slower to first token** at the
same prompt. Swapping models on the lean profile would therefore subtract nothing and
add ~1 s; variant 3 has no candidate worth measuring, which is a result in itself.

## What the lean profile would cost the robot (facts, not estimates)

From the default profile's own files and the live API surface:

- **Memory**: `memory.memory_enabled: true`, `user_profile_enabled: true`
  (`~/.hermes/config.yaml:111–113`); `memories/MEMORY.md` 2,215 B + `USER.md` 1,378 B are
  in every prompt today. A lean profile loses cross-session recall.
- **Skills**: 149 catalogue entries, of which the 11,066-character index is in the
  system prompt (`hermes prompt-size --platform api_server`). The robot loses the
  homelab skills it currently shares with the rest of the agent.
- **Tools**: the api_server platform currently exposes **18 toolsets / 57 tools**
  (`GET /v1/toolsets`), including `terminal` with `terminal.backend: local` and
  **no sandbox**, files, browser, code execution, web search, and the kanban/delegation
  surface. A device profile would keep none of that — and it would keep **no robot tools
  either**: the six body tools are not wired yet (`mcp_servers` in `~/.hermes/config.yaml`
  holds only `unifi` and `baserow`; the MCP bridge is a later task). A lean profile built
  today would give the robot turns *no tools at all*.
- **The live-data ability in the bench utterance** comes from the web tool. Without it the
  robot answers "what's the weather" from stale model knowledge — the exact question this
  card benchmarks.

## Recommendation

**Keep the default profile as the robot's brain for now.** On the evidence:

1. The 7.1–8.4 s is **not** a prompt-size problem: prefill is ~0.65 s. Nothing a lean
   `SOUL.md` or a smaller skills index does can move TTFT materially.
2. The model is already the fastest on the provider; every alternative measured is
   slower. Do not swap.
3. What would move the answer is removing the tool loop — web search in particular. That
   is a product decision about what the robot can answer, not a tuning knob, and it must
   be measured as a lean arm before the default channel changes.
4. If the owner wants the answer faster than ~8 s, the order to try is: (a) approve A or B
   on t_12360673 and measure the lean arm (`jarvis-body`/`jarvis-device`), which removes
   tools + skills + memory together; (b) if the gain is real, decide whether the robot is
   allowed to lose live web answers and shared memory for it. (c) If not, accept ~8–9 s
   for the answer and keep the 1.2 s acknowledgement as the audible response — SC-001
   (1.55 s p50 / 1.64 s p90) is already met and does not depend on the brain.

Do not treat the acknowledgement as a fix for the answer: it is 1.2 s of "Right." followed
by 8–9 s of silence, and a listener hears the gap.

## Reproduce

```bash
cd gateway
# 20 turns on the default channel (key read from the default profile's .env, never printed)
JG_HERMES_API_KEY="$(grep -m1 '^API_SERVER_KEY=' ~/.hermes/.env | cut -d= -f2-)" \
JG_HERMES_BASE_URL=http://127.0.0.1:8642/v1 \
  .venv/bin/python -m tools.latency_bench --turns 20 \
  --device-id bench-p32-baseline --out /tmp/p32-baseline.jsonl
```

For the lean arm, once the owner decides: `hermes profile create …` (see t_12360673),
`GET /p/<profile>/v1/models` must answer 200, then the same bench with
`JG_HERMES_BASE_URL=http://127.0.0.1:8642/p/<profile>/v1`. The per-turn prompt count is
not on the bench's wire today (`BrainReply` drops the final chunk's `usage`,
`gateway/jarvis_gateway/brain.py:62`); t_12360673 adds a prompt probe to the bench on
`wt/p3.3-lean-brain`, which is the tool to use for that run.

## Not done here

- No Hermes profile exists as a result of this card. An empty `jarvis-body` profile was
  created while setting up the intended variant 2 and removed immediately when the
  coordinator's mid-run note arrived; the default profile, its key and the live API
  server were never touched. `hermes profile list` shows no such profile; `:8642` still
  answers with the same key and the same 16,707-token prompt.
- No change to any config, any API key, `JG_HERMES_BASE_URL`, or any service outside the
  gateway. No gateway code change.
- No lean-arm latency, no lean prompt-token count, no 20-turn run on a second profile.
- No reasoning-effort experiment: it is a per-profile/per-request setting and changing it
  on the default channel is the same kind of owner decision.
