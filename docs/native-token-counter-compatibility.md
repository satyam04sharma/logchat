# Default native token counter

The preserved `ModelProfile` → `LocalModels` path automatically constructs a
lazy native counter. No QA counter injection is needed. A compact physical plan
and its submissions share one owner; native intake shares it across grouping and
compaction under the existing absolute 300-second timeout. Standalone preflight
and production preserved generation own and close their own bounded sessions.
Injected counters retain their existing lifecycle. Mock HTTP clients retain their
existing standalone generation behavior; explicit preflight and compact planning
still use the automatic binding. Remote and non-preserved clients are unchanged.

## Exact supported installation

This binding supports the reviewed **macOS Ollama 0.34.4 bundle** at
`/Applications/Ollama.app/Contents/Resources`, accessible through same-host
loopback HTTP(S). It does not claim Linux, Windows, other Ollama builds, arbitrary
model aliases or remote backend support. `/usr/bin/cc` and operating-system
support for executing the pinned x86_64 worker are required, including translation
on an ARM host. No PID or private runner discovery is used.

The fixed approval constants in `pipeline/native_tokenizer/ollama.py` pin:

| Asset | SHA-256 |
| --- | --- |
| libllama.0.4.1.dylib | dc17adf7beda178a5ef8db9437b78287136e0e31689416319284c2e78a9d621a |
| libggml.0.24.0.dylib | 4b5e0f65f451ede9980c1f86b2416cc4e19200759b69db84c30d4d0925a319fc |
| libggml-base.0.24.0.dylib | 2fb5beacef60b220c5c033bca7fc2d6c4cc5cb126b80d684c9ba73bf804f12e8 |
| bundled ollama executable | bba8b79eac84ab092b9568978c1116e6c950eda588961b14d8608ac683a2af0b |
| mistral:7b manifest | 6577803aa9a036369e481d648a2baebb381ebc6e897f2bb9a766a2aa7bfbc1cf |
| Mistral GGUF | f5074b1221da0f5a2910d33b642efa5b9eb58cfdddca1c79e16d7ad28aa2b31f |
| Mistral template | 1ff5b64b61b9a63146475a24f70d3ca2fd6fdeec44247987163479968896fc0b |
| nomic-embed-text:latest manifest | 0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f |
| Nomic GGUF | 970aa74c0a90ef7482477cf803618e776e173c007bf957f635f1015bfcfef0e6 |

Assets resolve from `OLLAMA_MODELS`, or the same user's `~/.ollama/models`.
All manifest/config/template/parameter/license bytes are pinned as well. Source
symlinks are rejected; the installed loader aliases are never followed or changed.
The existing exact Mach-O closure stages verified versioned bytes under
`libllama.0.dylib`, `libggml.0.dylib`, and `libggml-base.0.dylib` in the owner's
private directory. Only the existing approved Apple system-library imports are
allowed. Unknown installed bytes cannot approve themselves.

Public version, tags and show metadata establish the provider's declared revision
and supported local backend. The executable is verified on disk; this is not
process attestation of a server impersonating the trusted local installation.
Discovery happens once per owner, with provider checks repeated before generation.
Disk revisions are checked on every count, including cache hits. Unknown model,
platform, template, backend, asset drift or unavailable compiler fails closed with
a fixed compatibility error instructing restoration of the reviewed installation
or selection of a supported profile. No paths, provider bodies or source content
are included in these errors.

## Request and lifecycle contract

The reviewed legacy no-history template renders exactly:

```text
[INST] {system}\n\n{prompt}[/INST]
```

`system` includes the existing safety suffix. UTF-8 bytes, Unicode, escaped JSON,
NUL and control-token spellings are passed unchanged. This framing has no leading
BOS to strip; the pinned completion transformation leaves it intact. Both
`add_special` and `parse_special` are true. JSON schema remains in the complete
request identity as grammar; it is not appended to rendered input.

The complete request is snapshotted before awaiting. Only the existing exact
fields/options are accepted: stream false, shift false, truncate true, temperature
0, context 8192 and output reserve 1200. The proof binds model manifest, template,
GGUF/backend/source/architecture/loader/flag identity and the complete request.
A bounded 512-entry cache retains hashes and counts, not prompts. Large GGUF
hashing, compilation and vocabulary opening occur once per owner in the existing
owned child. Count calls and resource teardown are serialized by the runtime;
success, failure, cancellation and deadline expiration free/reap the worker and
remove staging. No new inference context, weights or provider render request is
used for counting.

`LocalModels.embedding_token_session()` exposes Nomic frontend `(false,true)`
and physical `(true,true)` counts for an immutable complete `/api/embed` body
with model, already-prefixed input list and truncate false. It verifies the pinned
2048 context/768-dimensional metadata and reports prefix-v1. It does not add
prefixes, change the old embedding request/source recipes, submit embeddings,
refine chunks or claim the active embedding runner's loaded context. That later
stage must bind the actual embedding submission and independently verify its
runtime context before using these proofs for admission.

## Validation boundary

Offline integration tests substitute approved inert asset fixtures and a mock
HTTP transport, then exercise the **default** profile/client, exact closure and
real C-worker IPC. Synthetic counts prove wiring, framing, flags, caching and
ownership only. They do not establish native token-count parity.

Root must independently run actual default-adapter parity against the saved
complete Mistral render (7502 tokens) and retained Nomic inputs, including control
and Unicode cases, and review real lifecycle behavior. Embedding refinement,
fresh 1200/100K validation and release approval remain separate and blocked.
