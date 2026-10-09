"""Owner-scoped chat model settings. Secret values never enter database rows or responses."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from api.security import database, owner
from cli.secrets import delete_credential, read_credential, write_credential
from pipeline.models import LocalModels, ModelUnavailable, OpenAICompatibleModels, validate_endpoint

router = APIRouter(prefix="/settings", tags=["settings"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSettingsInput(Input):
    provider: str
    chat_model: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=8, max_length=1000)
    api_key: str | None = Field(default=None, max_length=4000)

    @field_validator("provider")
    @classmethod
    def provider_name(cls, value):
        if value not in {"ollama", "openai_compatible"}:
            raise ValueError("Choose ollama or openai_compatible.")
        return value

    @field_validator("chat_model")
    @classmethod
    def model_name(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Choose a valid model name.")
        return value

    @field_validator("api_key")
    @classmethod
    def key_value(cls, value):
        if value is not None and not value.strip():
            raise ValueError("API keys cannot be blank. Use null to clear the saved key.")
        return value

    def normalized_base(self):
        if self.provider == "ollama":
            if self.base_url is not None:
                raise ValueError("Ollama uses the local configured endpoint; omit base_url.")
            return None
        if self.base_url is None:
            raise ValueError("OpenAI-compatible providers require base_url.")
        base_url = validate_endpoint(self.base_url)
        if base_url.startswith("http://") and self.api_key:
            raise ValueError("API keys require HTTPS.")
        return base_url


class PullInput(Input):
    model: str = Field(min_length=1, max_length=200)


def _row(owner_id):
    with database(owner_id) as connection:
        return connection.execute(
            "SELECT provider,chat_model,base_url,api_key_ref FROM model_settings WHERE owner_id=%s", (owner_id,),
        ).fetchone()


def _public(row):
    if not row:
        provider, model, base_url, key = "ollama", os.getenv("LOGCHAT_CHAT_MODEL", "qwen2.5:1.5b"), None, False
    else:
        provider, model, base_url, key = row["provider"], row["chat_model"], row["base_url"], bool(row["api_key_ref"])
    return {"provider": provider, "chat_model": model, "base_url": base_url, "has_api_key": key,
            "embedding": {"provider": "ollama", "model": os.getenv("LOGCHAT_EMBEDDING_MODEL", "nomic-embed-text"), "dimensions": 768}}


def _retained_credential(previous, provider, base_url):
    if (provider == "openai_compatible" and base_url.startswith("https://") and previous
            and previous["provider"] == provider and previous["base_url"] == base_url
            and previous.get("api_key_ref")):
        return str(previous["api_key_ref"])
    return None


@router.get("/models")
def get_model_settings(owner_id: str = Depends(owner)):
    return _public(_row(owner_id))


@router.put("/models")
def put_model_settings(body: ModelSettingsInput, owner_id: str = Depends(owner)):
    try:
        base_url = body.normalized_base()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    previous_ref = None
    new_ref = None
    key_supplied = "api_key" in body.model_fields_set
    try:
        with database(owner_id) as connection:
            # Read and replace the credential reference under one owner lock, including
            # the first save where there is no existing row to lock.
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 190057))', (owner_id,))
            previous=connection.execute('SELECT provider,chat_model,base_url,api_key_ref FROM model_settings WHERE owner_id=%s',(owner_id,)).fetchone()
            previous_ref=str(previous['api_key_ref']) if previous and previous.get('api_key_ref') else None
            if body.provider == 'openai_compatible':
                if key_supplied and body.api_key is not None:
                    new_ref=write_credential(body.api_key,owner_id=owner_id,purpose='model_api',endpoint=base_url)
                    credential_ref=new_ref
                elif key_supplied:
                    credential_ref=None
                else:
                    credential_ref=_retained_credential(previous,body.provider,base_url)
            else:
                credential_ref=None
            row = connection.execute(
                """INSERT INTO model_settings(owner_id,provider,chat_model,base_url,api_key_ref)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT(owner_id) DO UPDATE SET provider=EXCLUDED.provider,chat_model=EXCLUDED.chat_model,
                     base_url=EXCLUDED.base_url,api_key_ref=EXCLUDED.api_key_ref,updated_at=now()
                   RETURNING provider,chat_model,base_url,api_key_ref""",
                (owner_id, body.provider, body.chat_model, base_url, credential_ref),
            ).fetchone()
    except Exception:
        if new_ref:
            delete_credential(new_ref)
        raise HTTPException(503, "Could not save model settings.") from None
    if previous_ref and previous_ref != credential_ref:
        delete_credential(previous_ref)
    return _public(row)


@router.post("/models/check")
async def check_model_settings(body: ModelSettingsInput, owner_id: str = Depends(owner)):
    try:
        base_url = body.normalized_base()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    try:
        if body.provider == "ollama":
            models = LocalModels(chat_model=body.chat_model)
        else:
            key = body.api_key
            saved = _row(owner_id)
            same = saved and saved["provider"] == body.provider and saved["chat_model"] == body.chat_model and saved["base_url"] == base_url
            if "api_key" not in body.model_fields_set and same and saved.get("api_key_ref"):
                key = read_credential(str(saved["api_key_ref"]), owner_id=owner_id, purpose="model_api",endpoint=base_url)
            if base_url.startswith("http://") and key:
                raise HTTPException(422, "API keys require HTTPS.")
            models = OpenAICompatibleModels(base_url, body.chat_model, key)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean", "enum": [True]}}, "required": ["ok"], "additionalProperties": False}
        result = await models.generate("Return exactly the supplied ok value as JSON.", {"ok": True}, schema)
        if result.get("ok") is not True:
            raise ModelUnavailable("Model check returned an invalid response.")
        return {"ok": True, "detail": "Structured output is available.", "model": body.chat_model}
    except HTTPException:
        raise
    except ModelUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except Exception:
        raise HTTPException(503, "Could not check the configured model.") from None


@router.post("/models/pull")
async def pull_model(body: PullInput, owner_id: str = Depends(owner)):
    del owner_id
    try:
        return await LocalModels().pull(body.model)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except ModelUnavailable as error:
        raise HTTPException(503, str(error)) from None


# Imported by api.main/routes wiring without coupling the model module to MCP.
from api.mcp_connections import router as mcp_router  # noqa: E402

router.include_router(mcp_router)
