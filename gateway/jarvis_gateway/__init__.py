"""Jarvis Device Gateway — xiaozhi v2.2.4 voice gateway for the StackChan robot.

The gateway is the only new network listener in this project: it speaks the
xiaozhi ESP32 WebSocket protocol to the robot and the OpenAI-compatible API of
the Jarvis brain on loopback.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
