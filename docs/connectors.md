# Log sources and onboarding

Railway and Vercel adapters are experimental in rc11: mocked subprocess contracts pass, but live authentication, permissions and returned timestamps still require acceptance against a selected test project. The local Docker/file/command/browser/custom-CLI acceptance results are in [the release report](qa/release-2026-10-07.md).

Every adapter produces the same normalized events and calls native semantic intake. None writes a separate keyword-only memory store. Model setup and source authentication are different steps. Native model setup supports loopback Ollama; Railway/Vercel tokens authenticate provider logs only.

`logchat install --start` selects a shared model, then asks for a source. Choose `later` to configure sources separately. For unattended installation:

```sh
logchat install --yes --start --source docker --project . --source-config docker-source.json
```

`docker-source.json` contains non-secret JSON such as `{"container":"my-app"}`. Do not put keys in config files or command arguments. Source configuration can also specify `since` (timezone-aware past timestamp), `interval_seconds` (1–300), `window_seconds` (1–300), `timeout_seconds` (1–60), and `cwd`.

## Supported native adapters

| Source | Connection | Capture behavior |
| --- | --- | --- |
| Application stdout/stderr | `logchat local connect --project . -- npm run dev` | Runs an explicitly selected command; capture ends when the command exits. |
| Local file | `logchat local connect --project . --log-file ./app.log` | Background watcher, saved byte cursor, restart catch-up, rotation/truncation gaps. Add `--from-start` for existing lines on first connection. |
| Docker | `logchat local connect-provider docker --project . --container my-app` | Finite `docker logs` windows; timestamps, stdout and stderr, local Docker CLI/daemon permissions. |
| Railway | See below | Deployment log windows using the installed Railway CLI and explicit project/environment/service. |
| Vercel | See below | Expanded log records using the installed Vercel CLI and explicit project/environment. Available history/fields depend on provider plan and CLI behavior. |
| Custom log CLI | See below | Bounded command returning timestamped NDJSON for supplied time windows. No shell evaluation. |
| Structured push | `LocalEmitter` / authenticated event API | Explicit batch intake; caller retries failed batches with stable event IDs. |
| Browser console | Explicit `connectors.browser_console` adapter | Caller forwards selected console records; an app’s HTTP port does not expose browser console logs. |

Native provider collectors currently require **macOS or Linux**. Windows provider collection reports unsupported; other native paths need their own platform checks. Provider CLIs must be installed separately. Logchat does not install or sign into them automatically.

## Credentials and cloud sources

Use an existing protected CLI login, hidden token prompt, or `--token-env VARIABLE_NAME`. A hidden token is stored outside the project in a mode-0600, source-scoped credential file; no literal token is stored in adapter metadata, agent prompts, status output or argv. Disconnect deletes that source’s stored credential, while preserving memory. Environment credentials must exist in the Logchat service process: set them **before starting it**, or stop/restart that instance. `--token-env` accepts a name, not a value.

```sh
logchat local connect-provider railway --project . \
  --provider-project YOUR_PROJECT_ID --provider-environment production \
  --service api --prompt-token

logchat local connect-provider vercel --project . --new-source \
  --provider-project YOUR_PROJECT_ID --provider-environment production --prompt-token
```

For Railway, project tokens and account tokens have different scopes. `--token-env RAILWAY_API_TOKEN` uses account-token mode. The hidden token prompt uses project-token mode (`RAILWAY_TOKEN`). Avoid setting both variables. Vercel uses `VERCEL_TOKEN`; add `--scope YOUR_TEAM` when needed. Explicit token selection does not change a provider’s permissions; create tokens with the permissions your source needs.

Official references: [Docker logs](https://docs.docker.com/reference/cli/docker/container/logs/), [Railway logs](https://docs.railway.com/cli/logs), [Railway login/token scopes](https://docs.railway.com/cli/login), [Vercel logs](https://vercel.com/docs/cli/logs).

## Custom CLI adapter

For an already-running service, use a read-only log query command, not its application startup command. Supply an argv JSON array with `{since}` and `{until}` placeholders:

```sh
logchat local connect-provider cli --project . --new-source \
  --argv '["my-log-cli","logs","--json","--since","{since}","--until","{until}"]'
```

The CLI must output one JSON object per line with a timezone-aware `timestamp` (or a supported alias) and `message`. Keys such as `level`, `service`, `duration_ms` and `status_code` become normalized metadata/metrics; extra fields remain input to compaction. Blank/omitted messages are labelled as omitted, not invented error facts. Unrelated textual diagnostics in stdout cause rejection. Commands run without a shell. No credential-bearing command arguments are accepted. Use a provider login or environment credential.

## Progress, retries and reconnecting

```sh
logchat local providers --project .
logchat local providers --project . --poll
logchat local status --project .
logchat local ask "What context exists about authentication failures?" --project . --json
logchat local disconnect --project .
```

Providers register background workers in the Logchat service and resume saved cursors after restart. Reconnecting the same bound source keeps its cursor; `since` only initializes new sources. Changing provider identity/service requires `--new-source`, which preserves existing history. Multiple configured sources can run in one project. The native environment defaults to `dev`, independently of the selected remote provider environment. The project binding identifies the most recently attached source, so disconnect acts on that source. For any other source, use authenticated `DELETE /projects/{project_id}/sources/{source_id}/provider`; source IDs are in provider status.

Each fetch is bounded by time, output bytes, event count and process deadline. Capped windows shrink and retry from the same cursor. Malformed output, auth/rate-limit failures, model failures or backpressure never advance an unaccepted window. The cursor advances only after the native core accepts durable compact jobs (or explicit temporary raw intake). Identical replays deduplicate by stable source-scoped IDs. Empty provider windows advance the observation cursor but never prove complete coverage. Indexed memory is a later stage than accepted intake.

Provider logs can expire before catch-up and can arrive late. The current five-second observation delay is not a guarantee of capturing late arrivals. Cursors and event counts describe observations, not complete traffic. Old Railway collection/legacy Docker history is not migrated automatically; do not run two collectors on the same events and assume exact totals.

## Authenticated API controls

Control authentication is required for source creation, provider configuration, polling and status. Source ingestion tokens cannot configure providers.

- `POST /projects/{project_id}/sources`: `name`, `environment`, `kind` (provider kind), optional app `port`.
- `POST /projects/{project_id}/sources/{source_id}/provider`: `kind`, non-secret `config`, optional `token`. Prefer the CLI hidden prompt for humans.
- `DELETE /projects/{project_id}/sources/{source_id}/provider`: stop that source; retain context.
- `POST /projects/{project_id}/providers/poll`: one eligible pass, respects cooldown/leases.
- `GET /providers` and project status: safe metadata and gaps, without credentials/command arguments.

These are local one-user control operations. The read-only agent MCP does not expose credential mutation. A user-authorized local agent can use the CLI with local credentials; never paste control tokens into prompts.
