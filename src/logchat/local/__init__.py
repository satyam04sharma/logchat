"""Portable, single-user logchat runtime.

Keep package import lightweight because the CLI imports lifecycle helpers before
starting the API process.
"""

__all__ = ["LocalStore", "create_app"]


def __getattr__(name):
    if name == "create_app":
        from .app import create_app
        return create_app
    if name == "LocalStore":
        from .store import LocalStore
        return LocalStore
    raise AttributeError(name)
