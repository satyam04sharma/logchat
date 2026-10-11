# Vocabulary-only tokenizer implementation checkpoint

This is a native bridge foundation, not the default production counter yet.
`LocalModels` still needs a verified runtime owner and automatic lazy binding.
No actual GGUF load, native tokenizer parity, context fit, deadline performance,
embedding refinement, installed-client or 100K acceptance is established here.

## Pinned source and ABI

Ollama [v0.34.4's LLAMA_CPP_VERSION](https://github.com/ollama/ollama/blob/b2da9e468af2479058ae18c6d908ed29de410684/LLAMA_CPP_VERSION)
pins b11081, resolved to llama.cpp commit
`161755f29e415e2c33efe906e91843c068efd664`.
`pipeline/native_tokenizer/sources.json` records the exact upstream paths and
SHA-256 of the seven unmodified headers (the public header and its transitive
include dependencies) and upstream MIT license. These files, the bridge sources and
manifest ship byte-for-byte in wheel and sdist; no native binary is distributed.
The offline C fixture is included in sdist to make ABI checks reproducible.

The C shim derives function pointer types from those declarations, including
`llama_model_params` passed and returned by value. Python must never reproduce
that struct. `lc_vocab_open` loads an explicitly supplied absolute library path,
resolves only default-params/model-load/vocab/tokenize/model-free, sets
`vocab_only=true` and `n_gpu_layers=0`, and retains one vocabulary. It calls no
backend initialization, context creation, encode/decode or inference functions.
Load/symbol/vocabulary failures clear the output handle and release resources.
`lc_vocab_close` consumes a live handle exactly once; NULL is harmless. The
owner must serialize count and close and must not pass stale pointers.

`lc_vocab_count` accepts a pointer plus explicit UTF-8 byte length (embedded NUL
is not a terminator), explicit boolean add/parse-special flags, and an explicit
token capacity. It probes the public tokenizer's required capacity, checks
INT32_MIN overflow, allocates at most 1,048,576 token slots, then verifies the
second call exactly matches the required size. Inputs are capped at 1,048,576
bytes. Invalid booleans, capacity violations and inconsistent results fail
closed with fixed numeric categories. Token IDs are wiped and freed; only the
count leaves the shim. These resource ceilings are not model context limits.

The POSIX loader and Clang/GCC type extension currently have **offline macOS
compilation/stub coverage only**. Linux or Windows production compatibility is
not certified. Importing the Python package has no runtime side effects.

## Required runtime owner before production binding

The bridge is an internal mechanism, not an authorization boundary: absolute
paths alone are not trusted. Before invoking it, the application must implement:

- Same-host loopback endpoint binding, exact Ollama executable/version,
  reviewed library and transitive dependency hashes, and secure source-verified
  runtime compilation/loading with no shared writable-cache injection.
- Frozen manifest, GGUF, template and config identities; trusted model-store
  resolution; rejection of unsupported assets, symlinks, replacement/revision
  changes, history, renderer/parser or remote routing. Missing assets must
  produce an actionable compatibility error without inference fallback.
- Bounded asynchronous lifetime, cancellation/deadline cleanup and deterministic
  vocabulary reuse/free. A cancelled Python thread is not proof that a native
  call stopped; use an owned killable process boundary or an equivalently
  verified cancellation mechanism. Native diagnostic text must not escape as
  source/model content. Verification must precede every native load.
- Exact immutable generation request binding and the frozen Mistral render
  `[INST] ` + system + two newlines + prompt + `[/INST]`. Schema remains in the
  original body/grammar and complete request hash, not appended to the prompt.
  Verify pinned provider BOS transformation and tokenizer flags from source.
- Separate reusable Nomic complete-input counting: frontend `(false,true)` and
  physical `(true,true)` on the original prefixed `search_document: ` or
  `search_query: ` strings. Bind GGUF/tokenizer identity; derive special tokens
  from it. Training metadata is not numeric proof of loaded embedding context.
- Automatic lazy production binding for preserving `LocalModels`, retaining
  endpoint/model/options/guard/timeout/exception/cancellation behavior and the
  unchanged 300-second planning deadline. No root monkeypatch is acceptable.

The existing complete physical planner already checks a fitting parent directly,
splits oversized parents, and verifies every final child before any compact
request. Do not enumerate unused hypothetical singletons or alter fields,
metrics, catalogs, source/privacy/selection contracts or embedding recipe bytes.

## Offline proof and root-owned parity

`tests/test_native_vocab_bridge.py` compiles the real shim against an inert C
library compiled with the same pinned header. It asserts by-value parameter
fields, vocabulary reuse, explicit flags, binary string lengths, allocation
limits, overflow/inconsistent return handling, missing symbols and cleanup.
Synthetic count 7 is a transport fixture only; no real token claims follow.
Build verification compares every packaged source byte in wheel and sdist.

After the complete owner/default adapter is reviewed, root must check the exact
saved AUTH request and unmodified hashes, rendered SHA
`c299e0e79442e5db68568352d6cb5b0262b194ca15fe7d59122b9c5e416c4851`
(17,785 bytes) and native count 7,502, plus Unicode/control-token/BOS cases.
The preserved 400-key/digest replay must remain unchanged and synthetic child
counts explicitly labeled until actual parity. Nomic parity, effective context,
lossless embedding refinement and all integration/acceptance gates remain
separate work. No real model/tokenizer execution belongs in these offline tests.
