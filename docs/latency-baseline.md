# Latency baseline (T026, T028)

**Measured**: 2026-10-31 ~13:40–13:46 EDT (17:40–17:46 UTC) on CT 914
(`172.16.90.14`), no robot attached, synthetic device only. whisper is the live
service on CT 912; the brain is the live Hermes API server on loopback (:8642);
edge-tts is the real `en-GB-RyanNeural` voice. Python 3.11.16 in the project
venv, PyAV 18.1.0, edge-tts 7.3.1 (pinned in `pyproject.toml` by T026).

This file is a baseline, not a benchmark: three real spoken turns plus five
synthesis runs per sentence length. T057 owns the 20-turn percentile run.

## edge-tts synthesis (T026)

The library is called in-process (no CLI on this host, plan "Discrepancies" #5).
"First audio" is the first audio chunk from `Communicate.stream()`; "total" is
the complete MP3. Exact command:

```bash
cd /home/hermes/projects/stack-chan-jarvis/gateway
.venv/bin/python <script>   # calls edge_tts.Communicate(text, "en-GB-RyanNeural")
```

| Text | chars | first-audio ms (5 runs) | median | total ms (5 runs) |
|---|---|---|---|---|
| "Good evening to you." | 20 | 400, 274, 273, 233, 205 | **273** | 489, 379, 387, 342, 330 |
| "The weather in New York is mild today, with a light breeze." | 59 | 603, 206, 183, 199, 201 | **201** | 776, 296, 336, 345, 309 |

Reading: a warm connection gives ~200 ms to first audio for a one-sentence
chunk, inside the plan's 200–400 ms budget. The first request of a process
carries a ~400–600 ms connection/TLS penalty, which is why the gateway
synthesises the first sentence while the brain is still streaming the rest
(FR-008) and why the first chunk of a cold process is the number to watch.

The voice is confirmed working; `mp3_to_pcm24` decoded the returned MP3 to
24 kHz PCM and framed it at exactly 1440 samples per 60 ms packet (T022 tests).

## A real end-to-end turn (T018, the MVP loop)

Two measurements, both through the synthetic device (`tools/fake_device.py`,
the port of `jarvis-hardware/sim/synthetic-device`):

### A. Live services, fake TTS (the `live` test)

Synthetic device → gateway `ws://127.0.0.1:<port>/xiaozhi/v1/` → real whisper →
real Hermes → fake edge-tts (1 s of silence MP3 per sentence). Three turns on
one session, from `tests/integration/test_loop.py`, run with:

```bash
export JG_HERMES_API_KEY=...        # from the gateway's env file; never committed
.venv/bin/python -m pytest tests/integration/test_loop.py::test_a_real_question_is_answered_and_kept_in_context \
    -m live -s
```

| Turn | utterance (real speech, flite) | frames | vad_end | stt | ttft | first_audio | tts_end |
|---|---|---|---|---|---|---|---|
| 1 | "Jarvis, what is the weather like today?" | 72 | 3.8 | 1001.8 | 5926.9 | 43.7 | 424.1 |
| 2 | "Remember the word lantern. Reply with just the word stored." | 18 | 3.6 | 1018.5 | 2682.8 | 126.2 | 13.1 |
| 3 | "What word did I just ask you to remember?" | 18 | 3.0 | 1039.5 | 3716.5 | 153.1 | 29.0 |

Transcripts came back exactly as spoken, and turn 3 answered "lantern" from the
same conversation.

### B. Real process, real whisper + Hermes + edge-tts (the CLI)

From a clean clone of the branch (`git clone … && make install`), `python -m
jarvis_gateway` on `127.0.0.1:8010`, then:

```bash
.venv/bin/python -m tools.fake_device --url ws://127.0.0.1:8010/xiaozhi/v1/ \
    --token "$JG_DEVICE_TOKEN" --wav tests/fixtures/audio/utterance_speech.wav \
    --expect-loop --out-dir /tmp/turn-audio
```

Output: `session d59c57a6… open` / `sent 42 uplink frames (2520 ms) in 2.56s` /
`tts stop after 24.62s; 633 audio frames (37980 ms)` / `--expect-loop: sequence
OK` / exit 0. The gateway's own log record for that turn:

| stage | cumulative ms | delta ms |
|---|---|---|
| vad_end_ms | 300.6 | 300.6 (silence tail, not the device's stop) |
| stt_ms | 1251.4 | 950.8 |
| ttft_ms | 7606.3 | 6354.9 |
| first_audio_ms | 12316.0 | 4709.7 |
| tts_end_ms | 22057.1 | 9741.1 |

4 sentences, 633 downlink packets (37.98 s of audio), emotion `neutral`. The
captured downlink was decoded with the gateway's own Opus decoder and
re-transcribed by whisper:

> "Clear and dry over Winchester, Massachusetts, 52 degrees Fahrenheit, though
> the wind out of the northwest at 11 mph, gusting 17, makes it feel more like
> 42. Humidities down at 24% … Higher 53, dropping to 38 tonight, sunset at
> 1738 …"

i.e. the bytes on the wire are the answer, at 24 kHz Opus, one 60 ms packet per
frame.

A run of the same command at an earlier commit (23bc751) gave 601 frames in
19.9 s with the same shape of timings (ttft 6.57 s, first audio 10.15 s), so the
spread is the brain's, not the gateway's. One further run did **not** finish
inside the device's 30 s wait at all: two `tts sentence_start` frames had
arrived and the turn was still streaming when the synthetic device gave up (the
brain had just answered several probe turns). The gateway stayed inside its own
cap and cancelled cleanly when the socket closed; `tools/fake_device.py` now
waits 60 s by default. Expect TTFT variance until T057 measures the 20-turn
distribution.

### Against the budget

| Stage | Plan target | Measured (these turns) | Verdict |
|---|---|---|---|
| VAD close after last voiced frame | 300–600 ms | 3–4 ms when the device's `listen stop` closes it; 300 ms when the silence tail does | inside |
| whisper STT | 200–500 ms | 927–1040 ms | **above** |
| Hermes first token | 800–2000 ms | 2683–6568 ms | **above**, materially |
| edge-tts first sentence | 200–400 ms | 201–273 ms median for a 20–60-char sentence; 2355 ms when the brain's first sentence is long | **above** for long first sentences |
| Opus encode + WS per sentence | 20–60 ms/frame budget | 13–424 ms per turn for 18–72 frames; 7.2 s for 5 sentences/36 s of audio | inside (send is not playback) |

SC-001 (< 3.0 s median, < 4.0 s p90, last voiced frame → first audible word)
therefore **cannot be met on this host as the brain runs today**: in the real
process the first audible word arrived 12.32 s after the last voiced frame —
6.35 s of Hermes TTFT, 0.95 s of whisper, 4.71 s to synthesise the first
sentence (an earlier run: 10.15 s total, 6.57 s TTFT, 2.35 s synthesis). The
gateway's own plumbing (VAD close, Opus encode, send) contributes well under
150 ms. Two fixes belong to later cards, not this one: the brain's ~16.7 k-token
prompt/tool budget (T011, T057) and a shorter first sentence from the chunker
when the brain's opening sentence is long. T057 owns the 20-turn percentile run;
this file records the number so that conversation starts from evidence.

## Twenty consecutive turns (SC-004)

`tests/integration/test_loop.py::test_twenty_consecutive_turns_complete`
(marker `slow`, fake upstreams): 20 turns on one session, every one
`hello → stt → llm → tts start → sentence_start → frames → tts stop`, no
unhandled error, in **3.3 s** total (wall clock, `pytest -m slow`). The audio is
sent unpaced in that test, so the number measures the pipeline, not real time.

## P3.1 — first audible word under 3 s (implementation and 20-turn A/B)

**Measured**: 2026-10-31 13:55–14:32 EDT on CT 914 (`172.16.90.14`), no robot
attached, load average 1.3–2.1 on 8 cores. Real whisper on CT 912, real Hermes
API server on loopback (:8642), real `en-GB-RyanNeural` edge-tts. The number in
the card (12.32 s at `d0de930`) is from the same host; this section records the
20-turn before/after the card asks for.

### What changed

Two changes, independently configurable so each can be measured alone:

1. **Instant acknowledgement** (`gateway/jarvis_gateway/ack.py`, `turn.py`).
   If the brain has produced no speakable sentence `JG_ACK_DELAY_MS` (default
   300 ms) after the transcript, the gateway speaks one short phrase
   (`JG_ACK_TEXT`, default `Right.`) and then the answer. The phrase is
   synthesised once with the configured voice, decoded and Opus-framed, kept in
   memory, and cached in `JG_ACK_CACHE_DIR` (default `gateway/.cache/`) under a
   digest of voice + text, so later boots pay no synthesis and no decode. A
   corrupt cache is discarded and rebuilt.
2. **First-chunk cap** (`chunker.py`). The first chunk of a turn is emitted at
   the earlier of a clause boundary (`,` `;` `:` `—` or ` - `) once at least four
   words are buffered, or `JG_FIRST_CHUNK_WORDS` words (default 12). Every later
   chunk keeps the sentence rules. `0` disables the cap and restores the old
   behaviour exactly.

Wire shape in an acknowledged turn:

```text
stt → llm(neutral, "Right.") → tts start → sentence_start("Right.") → ack frames
    → llm(<emotion>, first answer chunk) → sentence_start(<chunk>) → answer frames
    → tts stop
```

Two decisions are worth recording because the vendor sources are not in this
repository to check against:

- **The acknowledgement is spoken after STT, not at utterance close.** FR-006
  makes an empty transcript end the turn with no further messages, and the
  silence/noise contract tests assert exactly that. Speaking before the
  transcript would say "Right." to a door slam and break that rule, so the
  gateway waits for a non-empty transcript. The cost is the whisper time
  (~1.25 s in the table below); the gain is not talking to noise.
- **A second `llm` message carries the answer.** The plan lists `llm` in the
  server→device set without a once-per-turn rule, and the display text has to
  change from "Right." to the answer anyway; the answer's `llm` also carries the
  emotion chosen from its first chunk. `tools/fake_device.py::check_loop_sequence`
  now accepts `llm` between `tts start` and `tts stop` and still refuses unknown
  messages there.

`first_audio_ms` in the turn record now marks the **acknowledgement** when one is
played (that is the first audible word, which is what SC-001 measures). In such
a turn `first_audio_ms < ttft_ms`, and `first_audio_ms − ttft_ms` is no longer
the synthesis latency of the answer. The bench below measures the answer's own
first frame from the device side.

### Method

`gateway/tools/latency_bench.py` starts the gateway in-process on an ephemeral
port against the live services, drives one synthetic device on one session for
20 turns (default WAV `tests/fixtures/audio/utterance_speech.wav`), and records
per turn: `listen stop` → first binary frame, the first frame of the answer, and
the gateway's own `turn` record (STT, TTFT, stage deltas). Each configuration
used a fresh `--device-id`, so every run started a fresh Hermes conversation
scope and the 20-turn context growth is the same in all four runs.

```bash
cd gateway
set -a; . /etc/jarvis-gateway/env; set +a     # or the host env file with API_SERVER_KEY
# D: both changes (the shipped default)
.venv/bin/python -m tools.latency_bench --turns 20 --device-id bench-d --out /tmp/p31-d.jsonl
# A: baseline (main, no ack, no cap) — run with the main checkout's code on PYTHONPATH
JG_ACK_TEXT= .venv/bin/python -m tools.latency_bench --turns 20 --device-id bench-a --out /tmp/p31-a.jsonl
# single-change runs: JG_ACK_TEXT= (cap only), JG_FIRST_CHUNK_WORDS=0 (ack only)
```

Percentiles are linear interpolation over the 20 answered turns; failed turns
(apology) are excluded from the latency distribution and counted separately —
there were none in any of the four runs. TTFT p90 is the largest source of
run-to-run noise (the same code moved p50 by ~2 s between runs); the
`token → answer` column isolates the chunker from that noise.

### Before/after, 20 turns each

| Change | First audible p50 | First audible p90 | Whisper p50 | Hermes TTFT p50 | First token → answer p50 |
|---|---|---|---|---|---|
| A. baseline (`d0d3c97`, no ack, no cap) | 12.36 s | 18.72 s | 1.24 s | 8.31 s | 3.17 s |
| B. cap only (`ACK_TEXT=""`, 12 words) | 11.22 s | 23.09 s | 1.25 s | 9.75 s | 1.18 s |
| C. acknowledgement only (`FIRST_CHUNK_WORDS=0`) | **1.55 s** | **1.65 s** | 1.25 s | 9.78 s | 1.99 s |
| D. both (shipped default) | **1.55 s** | **1.64 s** | 1.25 s | 7.11 s | 1.00 s |

("First audible" is on the gateway clock, i.e. from the last voiced frame —
the same origin as the card's 12.32 s. From `listen stop` instead of the VAD
tail, D is 1.25 s p50 / 1.34 s p90.)

**SC-001: PASS.** D is 1.55 s p50 / 1.64 s p90 against a 3.0 s / 4.0 s budget.
The acknowledgement, not the answer, is what makes the robot audible in time:
even the fastest 20-turn baseline run was 12.4 s p50, and no chunker change can
move the answer ahead of Hermes' 5–27 s TTFT on this host.

**Acknowledgement latency: 1.55 s from the last voiced frame, not the 400 ms
sub-target.** The budget is 1.25 s whisper + the 300 ms quiet window + ~50 ms
to send the first cached frame. On the gateway clock the ack itself lands
301 ms after the transcript (p50 and p90) and is independent of the brain. A
400 ms utterance-close target would require speaking before transcription,
which FR-006 and the silence/noise tests forbid. If the product wants that,
it is a spec change (acknowledge on VAD close, cancel it if whisper returns
nothing) and needs its own card.

**First-chunk cap: 1.0–1.2 s from first token, not the 800 ms sub-target.** The
cap cuts the wait from 3.17 s p50 / 4.33 s p90 to 1.00–1.18 s p50 / 1.72 s p90
(and the long-tail max from 8.1 s to 2.6 s). What remains is the brain's own
token rate for 10–12 words plus edge-tts first audio (~200–400 ms). Ten words
instead of twelve would buy perhaps 150 ms at a prosody cost; going below ~800 ms
reliably needs either a smaller first chunk (6–8 words) or a pre-opened edge-tts
connection. Neither is in this card's "10–12 words or a clause boundary".

### Item 3 — whisper (measured, not changed)

Twenty standalone transcriptions of the same `utterance_speech.wav` through
`SttClient`, warm connection, no gateway:

| n | p50 | p90 | min | max | transcripts identical |
|---|---|---|---|---|---|
| 20 | **976 ms** | 1079 ms | 898 ms | 1229 ms | yes |

In-turn `stt_ms` was 1.24–1.25 s p50 across all four runs (the delta is the
gateway's WAV build/upload and contention with the rest of the turn). Both are
2–5× the plan's 200–500 ms. Measured options, in order of likely gain and
risk, for a card of its own on CT 912 (this card does not touch it):

1. `faster-whisper` / CTranslate2 `int8` with the same model size — typically
   2–4× faster on CPU; the request/response contract is unchanged, so the
   gateway needs no change.
2. A smaller model (`small`/`base` en) — largest win, some accuracy risk; the
   fixture utterances transcribe exactly today, so it is measurable.
3. Upload while the user is still speaking — saves the ~50–100 ms WAV/HTTP
   upload, not the model time; not worth it before 1 or 2.

**Re-measured 2026-10-31 14:38 (P3.2, read-only):** 20 calls → **1025 ms p50 /
1131 ms p90** (min 912, max 1178), all transcripts identical. `/health` reports
`{"model":"base"}`, so option 2 above is largely the status quo — the only
remaining shrink is `tiny`, which carries real accuracy risk. And CT 914 cannot
administer CT 912 (both SSH keys denied, no `pct`/`qm`), so this cannot be
executed from a gateway card at all.

**Decision (coordinator, 2026-10-31): measured, not changed.** The
acknowledgement now puts the first audible word at 1.55 s with whisper inside
that budget, so the remaining ~600 ms would buy little; the 7.1 s brain TTFT is
the bottleneck that matters. `faster-whisper`/CTranslate2 `int8` on CT 912 stays
a documented lever for a homelab card with shell access, not a gateway one.

### Item 4 — brain TTFT (default channel measured; lean profile proposed)

The default device channel measured 7.1–9.8 s TTFT p50 (12.3–22.2 s p90) across
the four 20-turn runs, with a ~16.7 k-token prompt (T011). That is the number
the acknowledgement hides but does not fix.

A lean device channel was **not measured in this run**. It needs a second
Hermes profile (short SOUL, no memory provider, disabled toolsets) with its own
`API_SERVER_KEY`, addressed as `/p/<profile>/v1` on the loopback listener; that
is a host-level change to the shared Hermes install, not a gateway change, and
it should be an owner-approved action rather than something a worker spawns
under an unrelated latency card. The recipe is:

```bash
hermes profile create jarvis-device --clone-from Jarvis --no-skills
# then, in ~/.hermes/profiles/jarvis-device/: short SOUL.md, memory provider off,
# toolsets reduced to none (the robot has body tools through MCP, not shell/file tools),
# and a distinct API_SERVER_KEY in that profile's .env
JG_HERMES_BASE_URL=http://127.0.0.1:8642/p/jarvis-device/v1 .venv/bin/python -m tools.latency_bench --turns 20 --device-id bench-lean
```

The honest trade, stated without pretending it was measured: the lean channel
gives up shared long-term memory and the full tool surface (terminal, files,
web, skills) for the robot's turns; what it buys is a system prompt of a few
hundred tokens instead of ~16.7 k, and therefore some part of the 7–10 s TTFT.
How much is the provider's queueing/scheduling versus prefill is unknown today;
the ~2 s figure in the card is a target, not a measurement. This belongs in a
follow-up card with the `jarvis-device` profile, before any product decision to
give the robot a separate brain scope.

### Not done here

- No change to CT 912, the Hermes install, or any service outside the gateway.
- No lean Hermes profile was created (see item 4).
- Abort/barge-in (T037) is still unimplemented; the acknowledgement shares the
  turn's cancel hook but T037 owns the wire behaviour.
- The 20-turn runs are same-host but not same-minute; the provider's TTFT moved
  by ~2 s p50 between runs. The acknowledgement numbers are tightly clustered
  (1.19–1.71 s over 80 turns), so the SC-001 verdict does not depend on that
  variance; the TTFT columns should be read as "on this host, under this load".
