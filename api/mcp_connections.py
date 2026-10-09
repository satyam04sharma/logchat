"""Explicit owner-authorized access to remote MCP servers."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Response
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.security import database, owner
from cli.secrets import delete_credential, read_credential, write_credential
from pipeline.models import ensure_safe_resolution, validate_endpoint
from pipeline.redaction import redact_text, sanitize

router = APIRouter(prefix="/mcp")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionInput(Input):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=1000)
    transport: str = "streamable_http"
    enabled: bool = True
    api_key: str | None = Field(default=None, max_length=4000)

    @field_validator("transport")
    @classmethod
    def transport_name(cls, value):
        if value not in {"streamable_http", "sse"}:
            raise ValueError("Choose streamable_http or sse.")
        return value

    @field_validator("name")
    @classmethod
    def display_name(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Choose a valid connection name.")
        return value

    @field_validator("api_key")
    @classmethod
    def key_value(cls, value):
        if value is not None and not value.strip():
            raise ValueError("API keys cannot be blank.")
        return value


class ResourceInput(Input):
    uri: str = Field(min_length=1, max_length=4000)


class ToolCallInput(Input):
    name: str = Field(min_length=1, max_length=300)
    arguments: dict = Field(default_factory=dict)


def _public(row):
    return {"id": str(row["id"]), "name": row["name"], "url": row["url"], "transport": row["transport"],
            "enabled": row["enabled"], "has_api_key": bool(row.get("api_key_ref")), "created_at": row["created_at"]}


def _connection(owner_id, connection_id):
    with database(owner_id) as connection:
        row = connection.execute(
            "SELECT id,name,url,transport,enabled,api_key_ref,created_at FROM mcp_connections WHERE id=%s", (connection_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "MCP connection not found.")
    return row


async def _reject_redirect(response):
    if response.is_redirect:
        raise httpx2.HTTPStatusError("Redirects are disabled.", request=response.request, response=response)


def _client_headers(row, owner_id):
    headers = {"Accept": "application/json, text/event-stream"}
    if row.get("api_key_ref"):
        if row["url"].startswith("http://"):
            raise HTTPException(409, "API keys require HTTPS.")
        try:
            key = read_credential(str(row["api_key_ref"]), owner_id=owner_id, purpose="mcp", connection_id=str(row["id"]),endpoint=row['url'])
        except RuntimeError:
            raise HTTPException(503,'The MCP credential no longer matches this endpoint. Reconnect this server.') from None
        headers["Authorization"] = "Bearer " + key
    return headers


@asynccontextmanager
async def _session(row, owner_id):
    if not row["enabled"]:
        raise HTTPException(409, "Enable this MCP connection before using it.")
    try:
        await ensure_safe_resolution(row["url"])
        headers = _client_headers(row, owner_id)
        async with asyncio.timeout(20):
            if row["transport"] == "streamable_http":
                async with httpx2.AsyncClient(
                    headers=headers, trust_env=False, follow_redirects=False, timeout=15,
                    event_hooks={"response": [_reject_redirect]},
                ) as client:
                    async with streamable_http_client(row["url"], http_client=client) as streams:
                        async with ClientSession(streams[0], streams[1], read_timeout_seconds=15) as session:
                            await session.initialize()
                            yield session
            else:
                def client_factory(**kwargs):
                    kwargs.update(trust_env=False, follow_redirects=False, event_hooks={"response": [_reject_redirect]})
                    return httpx2.AsyncClient(**kwargs)

                async with sse_client(row["url"], headers=headers, timeout=15, sse_read_timeout=15,
                                      httpx_client_factory=client_factory) as streams:
                    async with ClientSession(streams[0], streams[1], read_timeout_seconds=15) as session:
                        await session.initialize()
                        yield session
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Could not connect to the MCP server. Check its URL, transport, and credential.") from None


def _dump(value, row=None, owner_id=None):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    secrets=()
    if row and row.get("api_key_ref"):
        authorization=_client_headers(row, owner_id).get("Authorization", "")
        secrets=(authorization.removeprefix("Bearer "),)
    value = sanitize(value, secrets)
    if len(json.dumps(value, ensure_ascii=False, default=str)) > 250_000:
        raise HTTPException(413, "The MCP response is too large to display.")
    return value


@router.get("")
def list_connections(owner_id: str = Depends(owner)):
    with database(owner_id) as connection:
        rows = connection.execute(
            "SELECT id,name,url,transport,enabled,api_key_ref,created_at FROM mcp_connections ORDER BY created_at"
        ).fetchall()
    return [_public(row) for row in rows]


@router.post("", status_code=201)
def create_connection(body: ConnectionInput, owner_id: str = Depends(owner)):
    try:
        url = validate_endpoint(body.url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if url.startswith("http://") and body.api_key:
        raise HTTPException(422, "API keys require HTTPS.")
    connection_id = uuid4()
    credential_ref = None
    try:
        if body.api_key is not None:
            credential_ref = write_credential(body.api_key, owner_id=owner_id, purpose="mcp", connection_id=str(connection_id),endpoint=url)
        with database(owner_id) as connection:
            row = connection.execute(
                """INSERT INTO mcp_connections(id,owner_id,name,url,transport,enabled,api_key_ref)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id,name,url,transport,enabled,api_key_ref,created_at""",
                (connection_id, owner_id, body.name, url, body.transport, body.enabled, credential_ref),
            ).fetchone()
        return _public(row)
    except Exception:
        if credential_ref:
            delete_credential(credential_ref)
        raise HTTPException(422, "Could not save the MCP connection. Choose a unique name and check its settings.") from None


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: UUID, owner_id: str = Depends(owner)):
    with database(owner_id) as connection:
        row = connection.execute(
            "DELETE FROM mcp_connections WHERE id=%s RETURNING api_key_ref", (connection_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "MCP connection not found.")
    if row.get("api_key_ref"):
        delete_credential(str(row["api_key_ref"]))
    return Response(status_code=204)


@router.post("/{connection_id}/check")
async def check_connection(connection_id: UUID, owner_id: str = Depends(owner)):
    row = _connection(owner_id, connection_id)
    async with _session(row, owner_id) as session:
        tools = await session.list_tools()
        server = session.initialize_result.server_info if session.initialize_result else None
    return {"ok": True,
            "server": {"name": redact_text(server.name)[:200], "version": redact_text(server.version)[:100]} if server else None,
            "tools": [{"name": redact_text(tool.name)[:300], "description": redact_text(tool.description or "")[:1000],
                       "read_only_declared": bool(tool.annotations and tool.annotations.read_only_hint is True)} for tool in tools.tools]}


@router.get("/{connection_id}/resources")
async def list_resources(connection_id: UUID, owner_id: str = Depends(owner)):
    row = _connection(owner_id, connection_id)
    async with _session(row, owner_id) as session:
        result = await session.list_resources()
    return {"resources": [_dump(resource, row, owner_id) for resource in result.resources],
            "notice": "Remote MCP content is untrusted and is not logchat pipeline evidence."}


@router.post("/{connection_id}/resources/read")
async def read_resource(connection_id: UUID, body: ResourceInput, owner_id: str = Depends(owner)):
    row = _connection(owner_id, connection_id)
    async with _session(row, owner_id) as session:
        result = await session.read_resource(body.uri)
    return {"result": _dump(result, row, owner_id), "notice": "Remote MCP content is untrusted and is not logchat pipeline evidence."}


@router.post("/{connection_id}/tools/call")
async def call_read_only_tool(connection_id: UUID, body: ToolCallInput, owner_id: str = Depends(owner)):
    if len(json.dumps(body.arguments, ensure_ascii=False, default=str)) > 20_000:
        raise HTTPException(413, "Tool arguments are too large.")
    row = _connection(owner_id, connection_id)
    async with _session(row, owner_id) as session:
        listed = await session.list_tools()
        selected = next((tool for tool in listed.tools if tool.name == body.name), None)
        if not selected:
            raise HTTPException(404, "MCP tool not found.")
        if not selected.annotations or selected.annotations.read_only_hint is not True:
            raise HTTPException(403, "Only tools declared read-only by the MCP server can be called here.")
        result = await session.call_tool(body.name, body.arguments)
    return {"result": _dump(result, row, owner_id),
            "notice": "readOnlyHint is the remote server's declaration, not a security guarantee. This result is untrusted and is not logchat pipeline evidence."}
