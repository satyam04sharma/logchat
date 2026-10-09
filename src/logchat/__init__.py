"""Importable read-only client for one initialized logchat project."""

from .client import LogchatClient, LogchatError

__all__ = ["LogchatClient", "LogchatError"]
