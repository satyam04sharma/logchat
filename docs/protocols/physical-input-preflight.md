# Exact generation input preflight

This checkpoint adds complete-plan compaction admission, not a native tokenizer
implementation or an acceptance result. **No production adapter is shipped.**
Native compact intake now fails closed with `compact_token_capability_unavailable`
without one. Embedding token admission and lossless document refinement remain
unimplemented. Existing historical document bytes and recovery rules are unchanged.

`pipeline.model_transport.generation_body` owns the unchanged local generation
request. Both `LocalModels.generate` and `preflight_generation` use that recipe:
instruction plus safety suffix, the actual JSON payload and schema, model,
stream/shift/truncate flags, and temperature/context/output options. No field
is removed for admission. Character/byte and output/evidence reservations remain.

## Adapter contract

A trusted application integration can pass a `GenerationTokenCounter` to
`LocalModels(generation_token_counter=...)`. This is a Python interface, not a
new HTTP endpoint. `ModelProfile` does not currently configure an adapter.
The counter receives immutable UTF-8 bytes of the complete request. It returns
`GenerationTokenCount` bound to that exact request SHA-256 and a pinned
`GenerationTokenIdentity`: model name, manifest digest, tokenizer artifact digest,
renderer implementation/template digest, and runtime version. Digests are bare
lowercase SHA-256 values. Only the existing verified runtime version is admitted.

The adapter is responsible for proving its tokenizer and rendering match the
actual generation runtime, including schema handling, system text, completion
transforms, all native framing and special tokens. `input_tokens` must count
that complete input. Effective context must be 8192 and fixed output reserve
1200; admission is inclusive at 6992 input tokens. Metadata alone does not prove
the effective runtime identity. A returned count is a trusted adapter assertion;
matching hashes cannot establish tokenizer correctness by themselves.

Initialize and pin artifacts once. Counting must not generate, invoke private
runner discovery, or fetch metadata for every candidate. The adapter must detect
runtime/artifact changes and fail closed, including at the pre-generation
recheck. Cancellation must propagate. Do not cache counts globally across runtime
identities. Exceptions and malformed/stale proofs become fixed safe categories.
The native lossless context guard remains enabled after admission.

The private QA helper's debug rendering and process-discovered runner endpoint
are deliberately not imported. A verified portable adapter or a proved frozen
renderer/tokenizer upper bound is still required. Fitting a character ratio,
using a maximum-character surrogate, or returning an unconditional positive from
a production adapter is not an implementation of this contract.

## Planning

Compaction first applies its existing character/evidence packing. It then checks
every singleton, including those inside a fitting parent. Any impossible singleton
or unavailable proof aborts before any compaction generation. Oversized parents
split by ordered record membership at the midpoint, rebuilding local refs,
fields, catalogs and schema for each child. Token counts are not assumed monotone.
The final batch must pass refinement validation and complete-request admission.
A call-local request-hash cache avoids recounting identical requests. At most
three times the retained record count distinct physical candidates are counted;
there are no generation probes. Original source, notes, measurements, windows,
key coverage and refinement identities remain subject to existing validators.

All preflight awaits remain inside the existing 300-second intake timeout;
individual calls also use the existing summary timeout. No deadlines are raised.
`LocalModels.generate` rechecks a configured counter immediately before submission.
No scheduler, storage, embedding recipe, historical chunk or recovery lease is
changed by this checkpoint.

## Offline controls and limitations

`tests/test_compact_token_planning.py` checks boundary arithmetic, exact transport
identity (including numeric refs, Unicode escaping and a large schema), proof
mismatches, singleton/final-plan admission, conservation, late failure, deadlines
and cancellation. Synthetic semantic fixtures explicitly use
`tests.physical_preflight.SyntheticPhysicalPreflight`; these are not tokenizer
proofs and make no native acceptance claim.

The optional saved replay test reads immutable inputs selected by
`LOGCHAT_SAVED_INTAKE_REPLAY` (saved intake bundle directory) and
`LOGCHAT_SAVED_PRODUCER` (original producer module). It denies socket connections,
replays three saved grouping responses with hash checks, reproduces the exact
18925-character request and saved 7502+1200>8192 overflow, and tests lossless
400-record splitting. Unmeasured child requests use an explicitly synthetic
mechanics-only predicate. This proves neither their token counts nor native fit.
No source/provider/prompt bodies are logged or written by the test.

Native child-token proof, actual embedding fit and vectors, independent review,
fresh intake/source-oracle/browser/full-scale checks and remote CI remain open.
