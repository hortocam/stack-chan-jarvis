"""Placeholder entry point for ``python -m jarvis_gateway``.

T015 replaces this module with the aiohttp application wiring (``ws_app``).
Until that lands it starts no listener and no background task — a card that
stops after Phase 1 must not leave a half-built service listening on :8010.
"""

from __future__ import annotations

import sys

from . import __version__

_NOT_WIRED = "jarvis-gateway %s: service wiring lands in T015; nothing started."


def main(argv: list[str] | None = None) -> int:
    print(_NOT_WIRED % __version__, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
