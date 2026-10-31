"""Registers the shared fake-upstream fixtures from ``tests.fakes`` for pytest.

The fixtures themselves live in ``tests/fakes/__init__.py`` (T006) so a test
module can also import the fake classes directly.
"""

from tests.fakes import fake_edge_tts, fake_hermes, fake_whisper

__all__ = ["fake_edge_tts", "fake_hermes", "fake_whisper"]
