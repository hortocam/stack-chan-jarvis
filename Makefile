# Entry points for the Jarvis Device Gateway.
#
# All targets run inside gateway/ on purpose: pytest must see
# gateway/pyproject.toml [tool.pytest.ini_options] (asyncio_mode = "auto"),
# otherwise every async fixture fails and it reads like a missing plugin.

.PHONY: install test lint

install:
	cd gateway && python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'

test:
	cd gateway && .venv/bin/python -m pytest -q

lint:
	cd gateway && .venv/bin/python -m ruff check .
