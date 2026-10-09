import os

import httpx
import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from api.routes import router
from api.workspace import router as workspace_router
from api.settings import router as settings_router

app = FastAPI(title="logchat", version="0.3.0rc11")


app.include_router(router)
app.include_router(workspace_router)
app.include_router(settings_router)

# Reject browser-origin traffic outside the local API; MCP uses authenticated loopback HTTP.
@app.middleware("http")
async def local_browser_requests(request, call_next):
    origin=request.headers.get("origin")
    allowed={f'http://{host}:{port}' for host in ('127.0.0.1','localhost') for port in (os.getenv('API_PORT','8080'),os.getenv('WEB_PORT','3000'))}
    if origin and origin not in allowed:
        return JSONResponse({"detail":"Use the local logchat API."},status_code=403)
    return await call_next(request)

@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    checks = {}
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
            if conn.execute("select to_regclass('public.projects')").fetchone()[0] is None:
                raise RuntimeError("Migrations missing")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
        for name, url in (("auth", os.environ["AUTH_URL"] + "/health"),
                          ("rest", os.environ["REST_URL"] + "/"),
                          ("ollama", os.environ["OLLAMA_URL"] + "/api/tags")):
            try:
                response = await client.get(url)
                response.raise_for_status()
                checks[name] = "ok"
            except Exception:
                checks[name] = "unavailable"
    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse({"status": "ready" if healthy else "unavailable", "checks": checks},
                        status_code=200 if healthy else 503)


@app.get("/status")
def status():
    return {"phase": "local_pipeline", "pipeline": "independent scheduler and dataset builder",
            "chat": "retrieval, context remapping, and cited answers", "embedding_model": "nomic-embed-text",
            "raw_log_storage": False}
