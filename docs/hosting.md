# Hosting and runtime boundaries

The canonical release is the native pipeline:

Explicit source → normalized events → model-guided sections → compact model summaries and preserved structured fields → durable jobs → embeddings → SQLite/sqlite-vec and lexical index → scoped hybrid retrieval → model relevance filtering → evidence context for another agent.

The shared generation profile is used for grouping, summarizing and relevance checks. Embeddings use a separate compatible embedding model. There is no automatic model fallback and no solution-generation stage. This is hybrid retrieval with model-based relevance filtering, not a cross-encoder reranker.

## Public website and downloadable application

A static host can serve an explanatory site, screenshots and download links. Users install Logchat and run its service and models on their own machines. The website must not include log databases, credentials, local bindings, private recordings or historical customer evidence. Use `scripts/prepare_public_source.py` for a fresh source export; inspect the selected files before publishing. The Python package and source archive are downloads, not serverless applications.

## Private hosted service

The current service is designed for one OS user, loopback HTTP and local Ollama. It is not a public multi-user service. Run it on a persistent machine with Python 3.12+, Ollama and persistent state; access it on that machine or through an explicitly configured authenticated private tunnel. Keep its loopback binding. Remote capture sources need explicit adapters; entering a remote application's port does not capture its stdout or browser console.

Before offering a public hosted service, implement and test remote client authentication, tenant isolation throughout capture/jobs/search, per-tenant quotas, model endpoint authorization, TLS ingress, backups/restores, retention/deletion and concurrent workload limits. Static/serverless hosting alone cannot supply the persistent SQLite database and local inference runtime.

## Historical implementation

`compose.yaml`, the root `Dockerfile`, `api/`, `infra/`, `supabase/`, `web/` and the older commands in `cli/main.py` describe the earlier Postgres/pgvector stack. They remain for historical regression and migration work. They are not the default release pipeline. `make legacy-up` explicitly starts that historical stack from a source checkout; `make start` starts native Logchat. Native code still shares some modules under `pipeline/` and compatibility packages remain in the wheel. Removing those shared modules without extracting their contracts would break the current runtime.

The wheel no longer embeds `logchat/_stack`. Existing local state and historical installation directories are left intact. Do not switch an existing project's memory backend without an explicit migration.
