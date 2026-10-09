# Local settings for users and agents

Logchat settings are available through the browser, CLI and authenticated loopback API. The CLI uses the same API and validation as Settings; it prints JSON so a coding agent can inspect the result. The instance must be running. Use `--state-dir PATH` to address an isolated instance. `logchat local settings` is an alias for `logchat settings`.

**Shared model** means the model Logchat uses for grouping, summarization and relevance. **Connected coding assistant** means the external harness that queries Logchat through MCP. Selecting the shared model does not change your external harness, and selecting a harness does not create stage-specific models.

## Settings reference

| Setting or operation | Browser | CLI | API |
|---|---|---|---|
| Inspect current configuration | Settings | `logchat settings show` | GET `/settings/models`, `/settings/capture`, `/settings/ui` |
| List installed local generation models | Shared model → Load installed models | `logchat settings models` | GET `/settings/models/available` |
| Choose a different shared generation model | Shared model → Use shared model | `logchat settings model --model NAME` | PUT `/settings/shared-model` |
| Local model endpoint | Shared model → Local model endpoint | `logchat settings model --model NAME --endpoint URL` | Same model-selection request |
| Check configured availability | Selected model is checked when saving | `logchat settings check` | POST `/settings/models/check` |
| Capture policy | Log retention → Capture policy | `logchat settings capture --mode summary_only` or `--mode retain_until_summarized` | PUT `/settings/capture` |
| Temporary size limit | Log retention → Temporary size limit (MiB) | Add `--max-mb 100` to capture command | `max_bytes` in capture request |
| Original retention limit | Log retention → Retention limit (hours) | Add `--retention-hours 24` to capture command | `retention_seconds` in capture request |
| Environment controls visibility | Environment visibility | `logchat settings ui --environments` or `--no-environments` | PUT `/settings/ui` |

CLI options omitted from a capture update preserve their current values. `settings show` includes pending counts, failures and expiry counters. Environment visibility defaults false and changes presentation only; agent scope permissions are unchanged.

## Copyable examples

```sh
logchat settings show --state-dir /path/to/state
logchat settings models --state-dir /path/to/state
logchat settings model --state-dir /path/to/state --model mistral:7b
logchat settings check --state-dir /path/to/state
logchat settings capture --state-dir /path/to/state --mode retain_until_summarized --max-mb 100 --retention-hours 24
logchat settings capture --state-dir /path/to/state --mode summary_only
logchat settings ui --state-dir /path/to/state --no-environments
```

Use an installed generation model's exact name. Listing consults the configured loopback Ollama endpoint, not arbitrary local processes. Listings are bounded and may be truncated. Capability metadata does not prove summary quality; selection runs internal readiness checks before committing. No model is substituted automatically.

All generation stages switch together for subsequent work. A model change is rejected while intake/compaction is active; retry after that work finishes. Raw buffering can continue independently when explicitly enabled. Failed checks leave the previous profile in place. Existing memory summaries retain their original model provenance; switching does not rewrite them.

The embedding model/dimension/digest is fixed for an existing index. A proposed connection must preserve it; incompatible changes require a separate state or explicit migration. Embeddings are a separate capability, not a per-stage generation override. The supported native provider is local Ollama. Hosted API providers and cloud-backed models are not selectable in this native settings flow.

Capture defaults to summaries-only. Explicit retain mode stores originals temporarily, outside retrieval, until compact work commits or originals expire. The managed spool reserves database/journal overhead, so usable payload is below its disk cap. Full capacity rejects new input. Switching back retains earlier pending work until processing/expiry. See [the native architecture](architecture.md) for the storage boundary.

## Coding-assistant integration and other configuration

Connect Codex, Claude Code, or another MCP-capable assistant using the project-bound configuration printed by:

```sh
logchat local integration --project /path/to/project
```

Configure your harness to use that output. MCP remains read-only; an authorized local coding agent can invoke the settings CLI to make changes. Logchat does not choose or install the external assistant's model. The integration requires a bound project and protected local credentials; Settings and Connect agent provide copyable instructions. Credentials must not be pasted into prompts or reports.

Service port/state directory are startup options: `logchat start --port 8765 --state-dir PATH`. App capture is an adapter configuration: `logchat local connect --project PATH --logchat-port 8765 --port 8000 --log-file PATH --from-start`, or pass the app command after `--`. A port alone does not reveal logs. Disconnect with `logchat local disconnect --project PATH`. Model-free first setup is `logchat install --capture-only --state-dir PATH --start`.

Internal chunk budgets, scheduler timing, compression prompts and embedding dimensions are not currently user settings. Year-scale rollups and hosted-provider configuration remain unfinished. Model summaries remain unverified prose even when selected fields and exact metrics are validated.

## API payloads

```json
{"endpoint":"http://127.0.0.1:11434","model":"mistral:7b"}
```

```json
{"mode":"retain_until_summarized","max_bytes":104857600,"retention_seconds":86400}
```

```json
{"environments_enabled":false}
```

The API requires the instance's control authentication; source-ingestion credentials cannot change settings. GET responses expose model names and status, never model keys or source tokens. Prefer the CLI for local agents so credentials stay in protected files.

## Verified release candidate

For 0.3.0rc7, the installed CLI switched the shared generation model from Mistral to Qwen and back. Existing memory IDs, their original summary provenance, and the embedding configuration stayed unchanged. A nonexistent model was rejected without changing the saved profile. Capture policy and environment visibility were changed through the CLI, and the final query returned context with supporting memory. The Settings screen loaded the installed generation-model list, and desktop and 390-pixel layouts were inspected with no horizontal overflow.

The regression suite passed 548 tests and 177 subtests, with one skipped test. These checks establish settings behavior and installation compatibility; they do not establish summary factuality or year-scale retention.

## Source connections

After model setup, `logchat install --start` can select a log source. `logchat local connect-provider` configures Docker, Railway, Vercel or a custom log CLI; `logchat local providers` prints safe status. Provider credentials and source controls are separate from shared model settings. See [connectors](connectors.md) for all source flags, token modes and authenticated API routes.

Rejected native push intake returns HTTP 503 with content-free
`X-Logchat-Processing-Category` and `X-Logchat-Processing-Phase` headers.
`intake_busy_retry_required` means the bounded processing slot is occupied: retry
identical events after it becomes available. Model/protocol failures are separate
categories. File capture status includes per-source `error_category` and
`error_phase`, retained until a successful poll. A ready embedding connection does
not mean every source's last intake succeeded. These diagnostics contain no log
bodies, provider output or credentials.
