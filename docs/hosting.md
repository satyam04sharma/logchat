# Runtime and hosting

Logchat is a local single-user service with persistent SQLite/sqlite-vec state and an explicitly selected Ollama profile. It returns context to agents. A website may host the animated walkthrough and installation links; it does not run this service or the user's models.

Run the installed service on loopback. A private persistent host needs an explicit private access arrangement and source adapters. A shared public service would require separate tenant isolation, authorization, storage and inference design.

Historical Docker/Postgres deployment is preserved on the `archive/legacy-stack` branch. Main contains the native release; it does not ship a separate web deployment stack or bundled model weights.
