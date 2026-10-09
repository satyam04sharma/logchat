from datetime import datetime
from typing import AsyncIterator, Protocol
from pipeline.types import LogEvent


class ConnectorError(RuntimeError):
    """A safe error code only; never include upstream response bodies."""


class Connector(Protocol):
    async def probe(self, since: datetime, until: datetime) -> int | None: ...
    def fetch(self, since: datetime, until: datetime) -> AsyncIterator[LogEvent]: ...
