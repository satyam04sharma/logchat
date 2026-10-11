# Native generation context guard

Local generation requires an explicitly verified provider/backend combination.
The current allowlist is exactly Ollama `0.34.4` with a GGUF completion model and
no stored model message history. Versions before and after it, development
suffixes, missing metadata, and other backends fail before generation. This is
an evidence allowlist, not a claim about the earliest supporting version.
Maintainers can extend `VERIFIED_CONTEXT_GUARD_VERSIONS` only after reviewing the
same source paths and obtaining host-owned installed verification for the new
release/backend. No user bypass or new model option is added.

Both `remote_host` and `remote_model` must be absent or exactly the empty string.
Nonempty strings (including whitespace), null, booleans, numbers, objects, and
arrays are rejected independently before generation, including partial remote
metadata. In the pinned provider release, remote-backed models can proxy before
the local runner and its verified context guard. This restriction is rechecked
on every generation and reports a fixed error without provider field values.

Every generation rechecks bounded `/api/version` and `/api/show` metadata without
following redirects. This avoids caching evidence across daemon restarts or
model replacement. The runtime trusts those responses from its configured local
provider; it cannot attest a custom binary or atomically prevent replacement
between metadata and generation. It does not discover processes or internal
runner ports.

The generation request uses `shift:false` and `truncate:true`. In the verified
completion path, truncation enables exact native-tokenizer feasibility checking;
disabled shifting makes an overfull prompt return HTTP 400 before token removal.
The requested context, output, temperature, timeouts, and embedding truncation
policy remain 8192, 1200, zero, existing values, and `truncate:false` respectively.

The source evidence is pinned to Ollama commit
[`b2da9e468af2479058ae18c6d908ed29de410684`](https://github.com/ollama/ollama/tree/b2da9e468af2479058ae18c6d908ed29de410684):

- [Generate routing](https://github.com/ollama/ollama/blob/b2da9e468af2479058ae18c6d908ed29de410684/server/routes.go)
  passes request shifting to scheduling and completion; native template preparation
  can discard older model messages independently of shifting.
- [Scheduler](https://github.com/ollama/ollama/blob/b2da9e468af2479058ae18c6d908ed29de410684/server/sched.go)
  carries shifting into the runner configuration and reloads incompatible runners.
- [GGUF server](https://github.com/ollama/ollama/blob/b2da9e468af2479058ae18c6d908ed29de410684/llm/server.go)
  selects llama-server for GGUF; other model formats are not admitted.
- [Completion input checking](https://github.com/ollama/ollama/blob/b2da9e468af2479058ae18c6d908ed29de410684/llm/llama_server.go)
  uses the installed tokenizer, compares against `num_ctx - 1`, and rejects overflow
  with disabled shifting. With shifting enabled, its half-shrink formula explains
  the observed 4099-token result after overflowing an 8192 context: 4099 is not
  the backend's hard capacity or evidence that the context setting was ignored.
- [Rendered message preparation](https://github.com/ollama/ollama/blob/b2da9e468af2479058ae18c6d908ed29de410684/server/prompt.go)
  preserves system messages and the final user message while trimming older ones.
  Our supplied instruction and payload occupy those preserved positions. Nonempty
  stored model message history is rejected because it could be silently discarded
  before completion checking.

Only a bounded, duplicate-free HTTP 400 JSON error envelope with the known native
context prefix maps to `GenerationContextExceeded`. Its diagnostic is fixed and
contains no raw provider body. Oversized, malformed, duplicate, unrelated, and
other-status errors remain generic failures. Successful output still undergoes
strict duplicate-key and schema validation; length termination remains failure.
Cancellation propagates through metadata and generation operations.

This guard does not prove that all 1200 output tokens fit after the complete
rendered input. It also cannot establish model relevance or prose fidelity.
Compact JSON bytes are not token counts. The host owns installed exact tokenizer
full-render-plus-reserve checks and all actual acceptance generations. A host
using the provider's debug render facility must use `_debug_render_only` (with
its leading underscore); production does not use this facility. No actual model
query, fresh compaction, intake, or state-copy proof was run for this patch.

Run mocked regressions with:

```sh
python -m pytest -q tests/test_native_generation_context_guard.py tests/test_local_model_response_bounds.py tests/test_rag_record_view.py
```

These tests verify transport policy and bounded failure behavior; they do not
replace installed provider acceptance. Source evidence and the supplied installed
review establish the capability choice; host verification remains the release gate.
