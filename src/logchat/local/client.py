"""Reuse the project's read-only client for an isolated native binding."""
import json
import tomllib
from pathlib import Path

from logchat.client import LogchatClient, LogchatError, _local_api_url
from cli.secrets import read_credential

EMITTER_INTAKE_SECONDS = 310


class LocalLogchatClient(LogchatClient):
    def __init__(self, project, **kwargs):
        directory=Path(project).expanduser().resolve()
        try:
            config=tomllib.loads((directory/'.logchat/local.toml').read_text())
        except (OSError,ValueError):
            raise LogchatError('Run logchat local attach in this project first.') from None
        try:
            kwargs.setdefault('api_url',config['api_url'])
            kwargs.setdefault('credential_reader',lambda reference: read_credential(reference,purpose='local_control',project_id=config['project_id'],endpoint=config['api_url']))
        except KeyError:
            raise LogchatError('The native project configuration is invalid. Run logchat local attach again.') from None
        super().__init__(directory,**kwargs)

    def __enter__(self):
        try:return super().__enter__()
        except LogchatError:
            raise LogchatError('The native project session is unavailable. Run logchat local attach again.') from None

    def _request(self, method, path, body=None):
        try:return super()._request(method,path,body)
        except LogchatError as error:
            raise LogchatError(str(error).replace('logchat login', 'logchat local attach')) from None

    def host_timeline(self, category: str = "", limit: int = 100, start: str | None = None, end: str | None = None):
        from urllib.parse import urlencode
        parameters = {"category": category, "limit": min(200,max(1,limit)), "project_id": self.project_id}
        if start is not None: parameters["start"]=start
        if end is not None: parameters["end"]=end
        return self._request("GET", "/host/timeline?"+urlencode(parameters))

    def host_status(self):
        return self._request("GET", "/host/status")

    def memory(self, memory_id: str):
        """Read one supporting aggregate from this client's bound project."""
        from uuid import UUID
        try:
            import re
            identifier = str(memory_id) if re.fullmatch(r"[a-f0-9]{64}",str(memory_id)) else str(UUID(str(memory_id)))
        except (ValueError, TypeError, AttributeError):
            raise LogchatError("Supporting memory ID must be a semantic chunk ID or legacy UUID.") from None
        return self._request("GET", f"/projects/{self.project_id}/memories/{identifier}")

    def investigations(self):
        """List the latest 100 bound-project investigation headers."""
        return self._request("GET", f"/projects/{self.project_id}/conversations")

    def investigation_context(self, conversation_id: str, question: str = ""):
        from uuid import UUID
        from urllib.parse import urlencode
        try:
            identifier = str(UUID(str(conversation_id)))
        except (ValueError, TypeError, AttributeError):
            raise LogchatError("Investigation ID must be a UUID.") from None
        if len(question) > 2000:
            raise LogchatError("Recall question must be at most 2000 characters.")
        return self._request("GET", f"/projects/{self.project_id}/conversations/{identifier}/context?" + urlencode({"q": question}))

    def _read_config(self):
        try:return tomllib.loads((self.project_directory/'.logchat/local.toml').read_text())
        except (OSError,ValueError):raise LogchatError('The native project binding is unavailable.') from None


def binding(project):
    try:return tomllib.loads((Path(project).expanduser().resolve()/'.logchat/local.toml').read_text())
    except (OSError,ValueError):raise RuntimeError('Run logchat local attach --project PATH first.') from None


class LocalEmitter:
    """Explicit structured push. No filesystem tailing or implicit port interception."""
    def __init__(self, project):
        import httpx
        self.config=binding(project)
        token=read_credential(self.config['source_ref'],purpose='local_ingest',project_id=self.config['project_id'],source_id=self.config['source_id'],endpoint=self.config['api_url'])
        # Summary-only intake runs bounded local generation before acknowledging.
        self.client=httpx.Client(base_url=_local_api_url(self.config['api_url']),headers={'Authorization':'Bearer '+token},trust_env=False,timeout=EMITTER_INTAKE_SECONDS)

    def emit_batch(self, events, *, retry_busy=False, drain_deadline=None):
        if drain_deadline is not None:
            import asyncio
            return asyncio.run(self._emit_bounded(events, retry_busy, drain_deadline))
        import time
        deadline = time.monotonic() + EMITTER_INTAKE_SECONDS
        body = {'source_id': self.config['source_id'], 'events': events}
        while True:
            response = self.client.post('/projects/'+self.config['project_id']+'/events',json=body,
                                        timeout=max(.1, deadline-time.monotonic()))
            if (not retry_busy or response.status_code != 503 or
                    response.headers.get('X-Logchat-Processing-Category') != 'intake_busy_retry_required' or
                    time.monotonic() >= deadline):
                response.raise_for_status()
                return response.json()
            time.sleep(min(1, max(0, deadline-time.monotonic())))

    async def _emit_bounded(self, events, retry_busy, drain_deadline):
        """Cancel the complete delivery, including retries, at the drain budget.

        A callable observes child exit while delivery is already in progress.
        Each group also retains the normal 310-second intake bound.
        """
        import asyncio
        import time
        import httpx
        deadline = time.monotonic() + EMITTER_INTAKE_SECONDS
        if drain_deadline() is not None:
            deadline = min(deadline, drain_deadline())
        body = {'source_id': self.config['source_id'], 'events': events}
        try:
            async with asyncio.timeout(max(0, deadline-time.monotonic())):
                async with httpx.AsyncClient(base_url=self.client.base_url, headers=self.client.headers,
                                             trust_env=False, timeout=EMITTER_INTAKE_SECONDS) as client:
                    while True:
                        if drain_deadline() is not None:
                            deadline = min(deadline, drain_deadline())
                        remaining = deadline-time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError
                        async with asyncio.timeout(remaining):
                            response = await client.post('/projects/'+self.config['project_id']+'/events', json=body)
                        if (not retry_busy or response.status_code != 503 or
                                response.headers.get('X-Logchat-Processing-Category') != 'intake_busy_retry_required'):
                            response.raise_for_status()
                            return response.json()
                        await asyncio.sleep(min(1, max(0, deadline-time.monotonic())))
        except TimeoutError:
            raise httpx.TimeoutException('Wrapped capture drain deadline exceeded.') from None

    def emit(self,event):
        return self.emit_batch([event])

    def close(self):self.client.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
