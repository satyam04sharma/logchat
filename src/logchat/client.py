"""Project-bound local API client with no mutation or secret-management methods."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from types import TracebackType
from typing import Callable, Self
from urllib.parse import urlparse
from uuid import UUID

import httpx

from cli.runtime import runtime_dir
from cli.secrets import read_credential


class LogchatError(RuntimeError):
    """A bounded user-facing error that never includes upstream response data."""


def _stack_port(name: str, default: int) -> int:
    root = runtime_dir()
    value: str | None = None
    try:
        with (root / ".logchat/.secrets").open() as settings:
            for line in settings:
                if line.startswith(name + "="):
                    value = line.split("=", 1)[1].strip()
    except OSError:
        return default
    if value is None:
        return default
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise LogchatError("The local API port is invalid.")
    return int(value)


def _local_api_url(explicit: str | None) -> str:
    value = explicit if explicit is not None else os.getenv("LOGCHAT_API_URL")
    if value is None:
        value = f"http://127.0.0.1:{_stack_port('API_PORT', 8080)}"
    try:
        parsed = urlparse(value)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError):
        raise LogchatError("The local API address is invalid.") from None
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or not 1 <= port <= 65535):
        raise LogchatError("The API address must be local.")
    return value.rstrip("/")


class LogchatClient:
    """Synchronous context manager scoped to one initialized project directory."""

    def __init__(
        self, project: str | Path, *, api_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        credential_reader: Callable[[str], str | None] = read_credential,
    ) -> None:
        self.project_directory = Path(project).expanduser().resolve()
        self.api_url = _local_api_url(api_url)
        self._transport = transport
        self._credential_reader = credential_reader
        self._http: httpx.Client | None = None
        self._config = self._read_config()
        try:
            self.project_id = str(UUID(str(self._config["project_id"])))
            self.project_name = str(self._config["name"])
            self.default_environment = str(self._config.get("environment", "dev"))
            self._session_ref = str(self._config["session_ref"])
        except (KeyError, TypeError, ValueError):
            raise LogchatError("The project configuration is invalid. Run logchat init again.") from None

    def _read_config(self) -> dict:
        try:
            value = tomllib.loads((self.project_directory / ".logchat/config.toml").read_text())
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            raise LogchatError("This directory is not an initialized logchat project.") from None

    def __enter__(self) -> Self:
        try:
            record = json.loads(self._credential_reader(self._session_ref) or "")
            token = record["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError()
        except Exception:
            raise LogchatError("The local project session is unavailable. Run logchat login.") from None
        self._http = httpx.Client(
            base_url=self.api_url, headers={"Authorization": "Bearer " + token},
            timeout=240, trust_env=False, transport=self._transport,
        )
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _request(self, method: str, path: str, body: dict | None = None):
        if self._http is None:
            raise LogchatError("Use LogchatClient as a context manager.")
        try:
            response = self._http.request(method, path, json=body)
            if response.status_code == 401:
                raise LogchatError("The local project session expired. Run logchat login in the project directory.")
            response.raise_for_status()
            return response.json()
        except LogchatError:
            raise
        except (httpx.HTTPError, ValueError):
            raise LogchatError("The local logchat request could not be completed.") from None

    def status(self) -> dict:
        return self._request("GET", f"/projects/{self.project_id}/status")

    def environments(self) -> list[dict]:
        value = self._request("GET", f"/projects/{self.project_id}/environments")
        if not isinstance(value, list):
            raise LogchatError("The local logchat response was invalid.")
        return value

    def _environment_ids(self, names: list[str] | None) -> list[str]:
        selected = names or [self.default_environment]
        if not 1 <= len(selected) <= 4 or any(not isinstance(name, str) or not name for name in selected):
            raise LogchatError("Choose between one and four environments by name.")
        available = {row.get("name"): row.get("id") for row in self.environments() if isinstance(row, dict)}
        try:
            identifiers = [available[name] for name in selected]
            if any(identifier is None for identifier in identifiers):
                raise KeyError()
            return [str(identifier) for identifier in identifiers]
        except KeyError:
            raise LogchatError("One of the requested environments is not configured for this project.") from None

    def ask(
        self, question: str, environments: list[str] | None = None, *, start: str | None = None,
        end: str | None = None, timezone: str = "America/New_York", service: str | None = None,
    ) -> dict:
        if not isinstance(question, str) or not 1 <= len(question) <= 2000:
            raise LogchatError("Question length must be between 1 and 2000 characters.")
        if (start is None) != (end is None):
            raise LogchatError("Start and end must be supplied together.")
        return self._request("POST", f"/projects/{self.project_id}/ask", {
            "question": question, "environment_ids": self._environment_ids(environments),
            "timezone": timezone, "start": start, "end": end, "compare_start": None,
            "compare_end": None, "service": service,
        })

    def compare(
        self, question: str, start: str, end: str, compare_start: str, compare_end: str,
        environments: list[str] | None = None, *, timezone: str = "America/New_York",
        service: str | None = None,
    ) -> dict:
        if not all(isinstance(value, str) and value for value in (start, end, compare_start, compare_end)):
            raise LogchatError("Both complete comparison time windows are required.")
        if not isinstance(question, str) or not 1 <= len(question) <= 2000:
            raise LogchatError("Question length must be between 1 and 2000 characters.")
        return self._request("POST", f"/projects/{self.project_id}/ask", {
            "question": question, "environment_ids": self._environment_ids(environments),
            "timezone": timezone, "start": start, "end": end, "compare_start": compare_start,
            "compare_end": compare_end, "service": service,
        })
