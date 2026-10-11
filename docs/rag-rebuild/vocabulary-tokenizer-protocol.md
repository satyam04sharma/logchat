# Vocabulary-only tokenizer implementation checkpoint

This is a native bridge foundation, not the default production counter yet.
`LocalModels` still needs reviewed provider asset discovery and automatic lazy binding.
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

## Architecture-aware asynchronous owner

`pipeline.native_tokenizer.runtime.VocabularyRuntime` owns one source-compiled C
executable per explicit async context. An isolated Python bootstrap hashes the
supplied assets, selects the library CPU from bounded Mach-O (thin/fat) or ELF64
metadata, verifies/copies source bytes, compiles, checks the executable CPU, then
replaces itself with the worker using `execve`. No Python process uses ctypes or
loads the bridge/library. Apple cc receives an explicit `-arch x86_64` or
`-arch arm64`, independently of the Python interpreter architecture. Linux only
admits the host CPU; Windows and unrecognized formats/targets fail closed.
CPU inspection does not establish ABI or backend approval. OS execution support
(e.g. Intel translation on ARM macOS) is also required; failed startup is closed.

The absolute monotonic deadline is at most 300 seconds and includes hashing,
compilation, load, lock waiting and counts. Idle expiry, cancellation and native
stalls kill/reap the owned process group, including compiler descendants. Late
spawn and stalled-close paths retain bounded cleanup. Normal close frees the
vocabulary and library deterministically. The private build directory is removed;
there is no shared writable binary cache and no abandoned native thread.

Only the fixed system compiler is invoked, with a minimal environment excluding
loader injection, flags, credentials, Python paths and user-site settings. The
worker duplicates the private response descriptor, redirects stdout/stderr to
`/dev/null` before dlopen, and installs a typed `llama_log_set` quiet callback
before model open. That process owns the callback exclusively. No native string,
token ID, prompt, path or diagnostic is returned on the protocol descriptor.
All C inputs and the architecture selector have checked source hashes and ship
in wheel/sdist; no native executable ships and import has no native side effects.

The bootstrap alone receives a bounded JSON asset configuration (65,536 bytes).
After exec, IPC is fixed binary framing, network byte order:

- Request: `LCQ1`, uint32 operation, byte length, token capacity, flags (20 bytes).
  Operation 1 counts exactly the following length bytes, including embedded NUL.
  Flags bit 0 = add-special, bit 1 = parse-special; other bits are rejected.
  Both byte length and token capacity are bounded at 1,048,576.
- Operation 0 closes; all other fields must be zero. Unknown operations, malformed
  headers, oversized values and partial frames fail closed and release resources.
- Response: `LCR1`, uint32 status, uint32 count (12 bytes). A nonzero status must
  have zero count. Native statuses are only the bridge's fixed 1–8 categories;
  bootstrap errors use a finite 100–110 mapping. Ready has status 0 and CPU code
  1 (x86_64) or 2 (arm64) in the count slot. Subsequent success contains only count.

`Asset` inputs carry expected SHA-256 identities for the library/model/supplied
closure. The bootstrap hashes each file once. The owner snapshots regular-file,
symlink-free inode/size/mtime/ctime revisions before startup and checks after load
and before/after every measurement. Reuse does not rehash a multi-GB model per
candidate. A changed asset closes the owner; no proof is returned. These checks
detect substitution but do not eliminate a native-open race. They are primitives,
not discovery/loader trust policy or proof that a supplied closure is complete.

**The default production binding is still absent.** The next provider adapter
must enforce reviewed same-host provider/backend/dependency resolution, trusted
manifest/GGUF/template assets, full request/render/BOS identities and request-only
memoization within the planner deadline, then own lazy `LocalModels` integration.
Do not approve unknown assets by fresh self-hashing or claim actual parity from
stub tests. Missing assets need actionable compatibility errors at that boundary.
Nomic frontend `(false,true)` and physical `(true,true)` byte counting are available
through the same worker primitive, but Nomic metadata/digest/context admission
and generation binding remain separate unfinished work.

Offline tests compile the actual worker and bridge against an inert library,
including an explicit x86_64 library/worker from the current host Python. They
exercise framing, binary lengths/flags/limits, private diagnostic suppression,
source/asset/revision rejection, native stalls, cancellation, deadlines, late
spawn, deterministic normal free and process/build cleanup. Synthetic counts
encode byte length and flags; they are not real tokenizer or performance proof.
Saved actual 7,502-token Mistral/ten Nomic parity remains a root-only gate after
default integration and independent review. Existing planner, request budgets,
embedding bytes, 400-record replay and downstream acceptance gates are unchanged.
