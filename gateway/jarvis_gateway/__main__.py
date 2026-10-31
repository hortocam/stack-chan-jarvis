"""``python -m jarvis_gateway``: load configuration, then serve (T015).

Configuration is fail-fast (FR-015): a missing token stops start-up here with a
message naming the key, before any socket is bound. Logs are JSON lines and
drop transcripts unless ``JG_LOG_TRANSCRIPTS`` is enabled (FR-016).
"""

from __future__ import annotations

import logging
import os
import sys

from . import __version__
from .config import Config, ConfigError, load_config
from .logging import JsonLinesFormatter
from .ws_app import run

__all__ = ["configure_logging", "main"]


def configure_logging(config: Config) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLinesFormatter(include_transcripts=config.log_transcripts))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> int:
    del argv  # no command-line options: everything comes from the environment
    try:
        config = load_config(os.environ)
    except ConfigError as exc:
        print(f"jarvis-gateway {__version__}: configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(config)
    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
