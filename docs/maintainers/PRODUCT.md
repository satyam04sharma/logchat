# Product scope: 0.3.0rc12

Logchat captures explicitly selected logs and provides compressed historical context to agents. The native release candidate supports local files, wrapped app commands, structured push, explicit browser-console forwarding, and Docker/Railway/Vercel/custom log CLI adapters. A port identifies an app; it is not a log source.

One selected Ollama generation profile serves grouping, summaries and relevance. A separate embedding capability feeds SQLite/sqlite-vec, complemented by full-text retrieval. Output is scoped evidence and context, without solution generation. Model prose is unverified and compression can omit useful facts.

Setup configures the model, then sources. Human and user-authorized local agent controls are documented through CLI and authenticated APIs. The primary agent MCP stays read-only. Provider-token support does not mean hosted-model/API-key support.

The first public scope is one user on loopback, with local model runtime and explicit adapters. Year-scale storage guarantees, long-term consolidation, universal automatic capture, multi-user hosting, arbitrary model providers and automatic migration of old native/Docker history are not included.

See [README](README.md), [architecture](docs/architecture.md), [connectors](docs/connectors.md), and [release gates](docs/release-readiness.md).
