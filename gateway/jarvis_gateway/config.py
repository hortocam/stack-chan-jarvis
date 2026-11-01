"""Configuration for the gateway — every setting comes from the environment.

The plan's "Configuration Reference" is the spec of record: each `JG_*` key
below maps one-to-one onto a row of that table. Loading is fail-fast — an
incomplete or nonsensical environment stops start-up with a `ConfigError`
naming the offending key, because a voice gateway that half-starts is
indistinguishable from a crashed one to the person talking to it.

Unknown keys are ignored: the process inherits the whole environment and only
`JG_*` names it knows are meaningful.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

__all__ = ["Config", "ConfigError", "load_config"]

#: Where the one-off acknowledgement phrase is cached (overridable).
DEFAULT_ACK_CACHE_DIR = str(Path(__file__).resolve().parents[1] / ".cache")


class ConfigError(ValueError):
    """Raised when the environment cannot produce a usable `Config`."""


@dataclass(frozen=True, slots=True)
class Config:
    """Immutable, validated configuration."""

    bind_host: str
    bind_port: int
    public_base_url: str
    ws_path: str
    ota_path: str
    device_token: str
    firmware_version: str
    stt_url: str
    stt_timeout_s: float
    hermes_base_url: str
    hermes_api_key: str
    hermes_model: str
    hermes_timeout_s: float
    voice: str
    tts_timeout_s: float
    vad_silence_ms: int
    vad_min_speech_ms: int
    max_utterance_s: float
    session_idle_s: float
    control_socket: str
    log_transcripts: bool
    apology_text: str
    ack_text: str
    ack_delay_ms: int
    ack_cache_dir: str
    first_chunk_words: int
    ui_feed_enabled: bool


_DEFAULTS: dict[str, str] = {
    "JG_BIND_HOST": "0.0.0.0",
    "JG_BIND_PORT": "8010",
    "JG_PUBLIC_BASE_URL": "http://172.16.90.14:8010",
    "JG_WS_PATH": "/xiaozhi/v1/",
    "JG_OTA_PATH": "/xiaozhi/ota/",
    "JG_DEVICE_TOKEN": "",
    "JG_FIRMWARE_VERSION": "1.0.0",
    "JG_STT_URL": "http://172.16.90.12:8001",
    "JG_STT_TIMEOUT_S": "10",
    "JG_HERMES_BASE_URL": "http://127.0.0.1:8642/v1",
    "JG_HERMES_API_KEY": "",
    "JG_HERMES_MODEL": "hermes-agent",
    "JG_HERMES_TIMEOUT_S": "30",
    "JG_VOICE": "en-GB-RyanNeural",
    "JG_TTS_TIMEOUT_S": "15",
    "JG_VAD_SILENCE_MS": "300",
    "JG_VAD_MIN_SPEECH_MS": "250",
    "JG_MAX_UTTERANCE_S": "30",
    "JG_SESSION_IDLE_S": "120",
    "JG_CONTROL_SOCKET": "/run/jarvis-gateway/control.sock",
    "JG_LOG_TRANSCRIPTS": "false",
    "JG_APOLOGY_TEXT": "Sorry, I didn't catch that.",
    "JG_ACK_TEXT": "Right.",
    "JG_ACK_DELAY_MS": "300",
    "JG_ACK_CACHE_DIR": DEFAULT_ACK_CACHE_DIR,
    "JG_FIRST_CHUNK_WORDS": "12",
    "JG_UI_FEED_ENABLED": "false",
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _required(key: str, raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ConfigError(f"{key} is required and must not be empty")
    return value


def _int(key: str, raw: str) -> int:
    try:
        return int(raw.strip(), 10)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _float(key: str, raw: str) -> float:
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise ConfigError(f"{key} must be a finite number, got {raw!r}")
    return value


def _bool(key: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{key} must be true or false, got {raw!r}")


def _positive(key: str, value: float) -> float:
    if value <= 0:
        raise ConfigError(f"{key} must be greater than zero, got {value!r}")
    return value


def _http_url(key: str, raw: str) -> str:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{key} must be an http(s) URL with a host, got {raw!r}")
    return value


def _path(key: str, raw: str) -> str:
    value = raw.strip()
    if not value.startswith("/"):
        raise ConfigError(f"{key} must be an absolute path beginning with '/', got {raw!r}")
    return value


def load_config(env: Mapping[str, str]) -> Config:
    """Build a validated `Config` from a mapping of environment variables."""

    def get(key: str) -> str:
        return env[key] if key in env else _DEFAULTS[key]

    bind_port = _int("JG_BIND_PORT", get("JG_BIND_PORT"))
    if not 1 <= bind_port <= 65535:
        raise ConfigError(f"JG_BIND_PORT must be in 1..65535, got {bind_port}")

    vad_silence_ms = _int("JG_VAD_SILENCE_MS", get("JG_VAD_SILENCE_MS"))
    if vad_silence_ms <= 0:
        raise ConfigError(f"JG_VAD_SILENCE_MS must be greater than zero, got {vad_silence_ms}")

    vad_min_speech_ms = _int("JG_VAD_MIN_SPEECH_MS", get("JG_VAD_MIN_SPEECH_MS"))
    if vad_min_speech_ms < 0:
        raise ConfigError(
            f"JG_VAD_MIN_SPEECH_MS must not be negative, got {vad_min_speech_ms}"
        )

    ack_delay_ms = _int("JG_ACK_DELAY_MS", get("JG_ACK_DELAY_MS"))
    if ack_delay_ms < 0:
        raise ConfigError(f"JG_ACK_DELAY_MS must not be negative, got {ack_delay_ms}")

    first_chunk_words = _int("JG_FIRST_CHUNK_WORDS", get("JG_FIRST_CHUNK_WORDS"))
    if first_chunk_words < 0:
        raise ConfigError(
            f"JG_FIRST_CHUNK_WORDS must not be negative, got {first_chunk_words}"
        )

    return Config(
        bind_host=_required("JG_BIND_HOST", get("JG_BIND_HOST")),
        bind_port=bind_port,
        public_base_url=_http_url("JG_PUBLIC_BASE_URL", get("JG_PUBLIC_BASE_URL")),
        ws_path=_path("JG_WS_PATH", get("JG_WS_PATH")),
        ota_path=_path("JG_OTA_PATH", get("JG_OTA_PATH")),
        device_token=_required("JG_DEVICE_TOKEN", get("JG_DEVICE_TOKEN")),
        firmware_version=_required("JG_FIRMWARE_VERSION", get("JG_FIRMWARE_VERSION")),
        stt_url=_http_url("JG_STT_URL", get("JG_STT_URL")),
        stt_timeout_s=_positive(
            "JG_STT_TIMEOUT_S", _float("JG_STT_TIMEOUT_S", get("JG_STT_TIMEOUT_S"))
        ),
        hermes_base_url=_http_url("JG_HERMES_BASE_URL", get("JG_HERMES_BASE_URL")),
        hermes_api_key=_required("JG_HERMES_API_KEY", get("JG_HERMES_API_KEY")),
        hermes_model=_required("JG_HERMES_MODEL", get("JG_HERMES_MODEL")),
        hermes_timeout_s=_positive(
            "JG_HERMES_TIMEOUT_S", _float("JG_HERMES_TIMEOUT_S", get("JG_HERMES_TIMEOUT_S"))
        ),
        voice=_required("JG_VOICE", get("JG_VOICE")),
        tts_timeout_s=_positive(
            "JG_TTS_TIMEOUT_S", _float("JG_TTS_TIMEOUT_S", get("JG_TTS_TIMEOUT_S"))
        ),
        vad_silence_ms=vad_silence_ms,
        vad_min_speech_ms=vad_min_speech_ms,
        max_utterance_s=_positive(
            "JG_MAX_UTTERANCE_S", _float("JG_MAX_UTTERANCE_S", get("JG_MAX_UTTERANCE_S"))
        ),
        session_idle_s=_positive(
            "JG_SESSION_IDLE_S", _float("JG_SESSION_IDLE_S", get("JG_SESSION_IDLE_S"))
        ),
        control_socket=_path("JG_CONTROL_SOCKET", get("JG_CONTROL_SOCKET")),
        log_transcripts=_bool("JG_LOG_TRANSCRIPTS", get("JG_LOG_TRANSCRIPTS")),
        apology_text=get("JG_APOLOGY_TEXT"),
        ack_text=get("JG_ACK_TEXT").strip(),
        ack_delay_ms=ack_delay_ms,
        ack_cache_dir=_path("JG_ACK_CACHE_DIR", get("JG_ACK_CACHE_DIR")),
        first_chunk_words=first_chunk_words,
        ui_feed_enabled=_bool("JG_UI_FEED_ENABLED", get("JG_UI_FEED_ENABLED")),
    )
