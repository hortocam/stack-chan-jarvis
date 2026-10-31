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
