"""Unit tests for the `JG_*` configuration loader (T004).

The loader is the fail-fast boundary of the service: a missing token, a typo'd
URL or a nonsensical timeout must stop start-up rather than surface as a
half-working listener.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from jarvis_gateway.config import Config, ConfigError, load_config

GATEWAY_ROOT = Path(__file__).resolve().parents[2]

BASE_ENV = {
    "JG_DEVICE_TOKEN": "device-token-for-tests",
    "JG_HERMES_API_KEY": "hermes-key-for-tests",
}


def test_defaults_match_the_configuration_reference() -> None:
    cfg = load_config(BASE_ENV)

    assert isinstance(cfg, Config)
    assert cfg.bind_host == "0.0.0.0"
    assert cfg.bind_port == 8010
    assert cfg.public_base_url == "http://172.16.90.14:8010"
    assert cfg.ws_path == "/xiaozhi/v1/"
    assert cfg.ota_path == "/xiaozhi/ota/"
    assert cfg.device_token == "device-token-for-tests"
    assert cfg.firmware_version == "1.0.0"
    assert cfg.stt_url == "http://172.16.90.12:8001"
    assert cfg.stt_timeout_s == 10.0
    assert cfg.hermes_base_url == "http://127.0.0.1:8642/v1"
    assert cfg.hermes_api_key == "hermes-key-for-tests"
    assert cfg.hermes_model == "hermes-agent"
    assert cfg.hermes_timeout_s == 30.0
    assert cfg.voice == "en-GB-RyanNeural"
    assert cfg.tts_timeout_s == 15.0
    assert cfg.vad_silence_ms == 300
    assert cfg.vad_min_speech_ms == 250
    assert cfg.max_utterance_s == 30.0
    assert cfg.session_idle_s == 120.0
    assert cfg.control_socket == "/run/jarvis-gateway/control.sock"
    assert cfg.log_transcripts is False
    assert cfg.apology_text == "Sorry, I didn't catch that."
    # P3.1 latency: acknowledgement phrase, its delay, cache and first-chunk cap.
    assert cfg.ack_text == "Right."
    assert cfg.ack_delay_ms == 300
    assert cfg.ack_cache_dir.endswith("/.cache")
    assert cfg.first_chunk_words == 12


def test_every_key_can_be_overridden() -> None:
    env = {
        **BASE_ENV,
        "JG_BIND_HOST": "127.0.0.1",
        "JG_BIND_PORT": "9100",
        "JG_PUBLIC_BASE_URL": "http://172.16.90.14:9100",
        "JG_WS_PATH": "/other/v1/",
        "JG_OTA_PATH": "/other/ota/",
        "JG_FIRMWARE_VERSION": "2.3.4",
        "JG_STT_URL": "http://127.0.0.1:9101",
        "JG_STT_TIMEOUT_S": "2.5",
        "JG_HERMES_BASE_URL": "http://localhost:9999/v1",
        "JG_HERMES_MODEL": "hermes-test-model",
        "JG_HERMES_TIMEOUT_S": "12",
        "JG_VOICE": "en-US-AriaNeural",
        "JG_TTS_TIMEOUT_S": "3",
        "JG_VAD_SILENCE_MS": "450",
        "JG_VAD_MIN_SPEECH_MS": "120",
        "JG_MAX_UTTERANCE_S": "20",
        "JG_SESSION_IDLE_S": "60",
        "JG_CONTROL_SOCKET": "/tmp/control.sock",
        "JG_LOG_TRANSCRIPTS": "true",
        "JG_APOLOGY_TEXT": "Say again?",
        "JG_ACK_TEXT": "Certainly.",
        "JG_ACK_DELAY_MS": "120",
        "JG_ACK_CACHE_DIR": "/tmp/jarvis-cache",
        "JG_FIRST_CHUNK_WORDS": "9",
    }

    cfg = load_config(env)

    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 9100
    assert cfg.public_base_url == "http://172.16.90.14:9100"
    assert cfg.ws_path == "/other/v1/"
    assert cfg.ota_path == "/other/ota/"
    assert cfg.firmware_version == "2.3.4"
    assert cfg.stt_url == "http://127.0.0.1:9101"
    assert cfg.stt_timeout_s == 2.5
    assert cfg.hermes_base_url == "http://localhost:9999/v1"
    assert cfg.hermes_model == "hermes-test-model"
    assert cfg.hermes_timeout_s == 12.0
    assert cfg.voice == "en-US-AriaNeural"
    assert cfg.tts_timeout_s == 3.0
    assert cfg.vad_silence_ms == 450
    assert cfg.vad_min_speech_ms == 120
    assert cfg.max_utterance_s == 20.0
    assert cfg.session_idle_s == 60.0
    assert cfg.control_socket == "/tmp/control.sock"
    assert cfg.log_transcripts is True
    assert cfg.apology_text == "Say again?"
    assert cfg.ack_text == "Certainly."
    assert cfg.ack_delay_ms == 120
    assert cfg.ack_cache_dir == "/tmp/jarvis-cache"
    assert cfg.first_chunk_words == 9


def test_config_is_frozen() -> None:
    cfg = load_config(BASE_ENV)

    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.bind_port = 1234  # type: ignore[misc]


@pytest.mark.parametrize("missing", ["JG_DEVICE_TOKEN", "JG_HERMES_API_KEY"])
def test_missing_required_key_raises(missing: str) -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != missing}

    with pytest.raises(ConfigError) as excinfo:
        load_config(env)

    assert missing in str(excinfo.value)


@pytest.mark.parametrize("key", ["JG_DEVICE_TOKEN", "JG_HERMES_API_KEY"])
@pytest.mark.parametrize("value", ["", "   "])
def test_empty_required_key_raises(key: str, value: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config({**BASE_ENV, key: value})

    assert key in str(excinfo.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("JG_STT_URL", "not-a-url"),
        ("JG_STT_URL", "172.16.90.12:8001"),
        ("JG_HERMES_BASE_URL", "ftp://127.0.0.1:8642/v1"),
        ("JG_PUBLIC_BASE_URL", "/relative/path"),
    ],
)
def test_bad_url_raises(key: str, value: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config({**BASE_ENV, key: value})

    assert key in str(excinfo.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("JG_STT_TIMEOUT_S", "0"),
        ("JG_HERMES_TIMEOUT_S", "-1"),
        ("JG_TTS_TIMEOUT_S", "soon"),
        ("JG_BIND_PORT", "0"),
        ("JG_BIND_PORT", "70000"),
        ("JG_VAD_SILENCE_MS", "-5"),
        ("JG_MAX_UTTERANCE_S", "0"),
        ("JG_VOICE", ""),
        ("JG_WS_PATH", "xiaozhi/v1/"),
        ("JG_OTA_PATH", ""),
        ("JG_LOG_TRANSCRIPTS", "maybe"),
        ("JG_ACK_DELAY_MS", "-1"),
        ("JG_FIRST_CHUNK_WORDS", "-1"),
        ("JG_ACK_CACHE_DIR", "relative/cache"),
    ],
)
def test_invalid_values_raise(key: str, value: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config({**BASE_ENV, key: value})

    assert key in str(excinfo.value)


def test_unknown_keys_are_ignored() -> None:
    cfg = load_config({**BASE_ENV, "JG_NOT_A_REAL_KEY": "whatever", "PATH": "/usr/bin"})

    assert cfg.bind_port == 8010


def test_example_env_file_parses_against_the_loader() -> None:
    example = GATEWAY_ROOT / ".env.example"
    assert example.is_file(), f"{example} must exist — it documents every JG_* key"

    env: dict[str, str] = {}
    for raw in example.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()

    documented = {key for key in env if key.startswith("JG_")}
    expected = {
        field.name.upper()
        for field in dataclasses.fields(Config)
    }
    # Every documented key is consumed and the file sets every JG_* key the plan lists.
    assert {f"JG_{name}" for name in expected} == documented

    cfg = load_config(env)
    assert cfg.device_token
    assert cfg.hermes_api_key
