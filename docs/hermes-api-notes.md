# Live Hermes API contract (T011 spike)

**Date**: 2026-10-31 13:16 EDT (17:16 UTC) — **Host**: CT 914 (`172.16.90.14`), no robot attached
**Process**: the running Hermes API server, `http://127.0.0.1:8642/v1` (the plan's default; the
card's `8650` does not answer — plan "Discrepancies" #1).
**Key**: `API_SERVER_KEY` from `/home/hermes/.hermes/.env`, read at runtime; never printed, echoed
or written here. Commands below show the header name only.

This file records what the live server actually does. It is evidence for T021 (brain client),
T026/T057 (latency) and the operator-facing runbook; it changes no service code and no default
except where noted.

## What was run

The spike is a throwaway script (not committed) using `urllib.request` with
`Authorization: Bearer $API_SERVER_KEY`:

```bash
# model discovery
GET  http://127.0.0.1:8642/v1/models
# streamed completion, with and without the per-conversation header
POST http://127.0.0.1:8642/v1/chat/completions
     {"model": "hermes-agent", "stream": true, "messages": [...]}
     X-Hermes-Session-Key: stackchan-spike-2
```

Two-turn probes: turn 1 asks the brain to remember a nonsense word, turn 2 (same header) asks for
it back. A third probe sends the prior turn explicitly in `messages` (client-side history) as a
control.

## Results

| Check | Observed |
|---|---|
| `GET /v1/models` | `200`, `data[].id == ["hermes-agent"]` — matches `JG_HERMES_MODEL` |
| Streamed completion | `200`, `text/event-stream`, `data:` lines, terminated by `data: [DONE]` |
| Chunk shape | `chat.completion.chunk`; first chunk `delta: {"role": "assistant"}`; later chunks `delta: {"content": "..."}`; final chunk `delta: {}` with `finish_reason: "stop"` and a `usage` object (see `tests/fixtures/protocol/sse_chunk.txt`) |
| Extra delta field | some replies stream `delta.reasoning_content` before any `delta.content`; the gateway must read only `content` (T021) |
| Prompt size | `prompt_tokens` ≈ 16 700 for a one-line user message — the Hermes system prompt dominates. This is why TTFT, not token count, is the budget item (SC-003) |
| Response headers | `X-Hermes-Session-Id: api-<hex>` and the request's `X-Hermes-Session-Key` echoed back |
| Session key accepted | yes; requires API-key auth (server rejects the header with 403 when no key is configured) |
| Context on the next turn, same key | **seen** on the second run: turn 2 answered `ORANGUTAN` / `BLUEHERON` from turn 1 |
| Context without the key | **also seen** in the second run: turn 2 answered `KIWI` with no header |
| First attempt | turn 2 with the same key answered "no word — nothing in the session history". A retry minutes later worked, and a third probe passed with client-supplied history |

## Reading of the results

1. **Continuity is real but not instant.** The first probe's second request arrived back-to-back
   with the first and did not see it; the retry and the follow-up probe did. The API server resolves
   `X-Hermes-Session-Key` against a session row that appears to be written when the turn finishes,
   so an immediate follow-up can land in a fresh session. A real voice turn has STT + TTS between
   requests, but T021 must not assume zero-latency continuity, and T057 (20-turn loop) is where the
   behaviour gets measured.
2. **The key is memory scope, not client-side history.** With a stable key the server finds the
   conversation; without one it also answered from its default session in this probe. The gateway
   therefore still sends `X-Hermes-Session-Key: stackchan-<device-id>` (plan C6/D5) so scope is
   explicit, and must not rely on the server for *this turn's* prompt: it sends the user's message
   each time.
3. **Reasoning deltas are on the wire.** A parser that concatenates every `delta.content` is
   correct; one that concatenates the first string field it finds would speak the model's
   reasoning. T021's SSE test should include a `reasoning_content` chunk.
4. **Defaults confirmed, not changed.** `JG_HERMES_BASE_URL=http://127.0.0.1:8642/v1` and
   `JG_HERMES_MODEL=hermes-agent` in `gateway/.env.example` already equal the observed live values,
   so T011 needs no edit there. The port stays configuration, not a constant (plan D5).
5. **TTFT has a fixed cost before any token.** ~16.7 k prompt tokens per turn is the budget the
   plan's 800–2000 ms assumes; with the brain warm, first content arrived within the same second in
   these probes. The measured number belongs in `docs/latency-baseline.md` (T057), not here.

## Not done here

- No change to the running Hermes process, its environment, or its bind address (plan
  "Discrepancies" #2 remains an operator action).
- No load or latency benchmark (T026/T057 own that).
- No gateway code: T021 implements the client against the shape recorded above.
