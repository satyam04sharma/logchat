# Fresh compact source observations

Fresh `compact_summary_batch` emits `compact-evidence-v3` using
`source-observation-render-v2`. Historical v1/v2 storage, hashes, embedding bytes
and replay remain unchanged; active factual consumers use a version-aware
projection that excludes historical prose. Deterministic model fixtures exercise
this route. This protocol is not actual-model or native acceptance.

## Model selection contract

Fresh production uses `source-catalog-selector-v1`:

```json
{
  "phrase_refs": ["p0"],
  "important_fields": ["f0"]
}
```

The request supplies a lossless `phrase_catalog` table of `[phrase_ref, exact
text]` rows. It includes the complete original records and typed field catalog.
The model selects at most four supplied refs; every incoming ref is validated
before exact repeated refs are normalized. Extra keys, wrong types and unknown
refs reject the whole output. Field refs remain distinct and their union with
required correlation refs must contain at most 64 entries. No free-text fallback,
retry, replacement prose or budget increase is used.

The historical free-text `source_phrases` decoder remains available at its direct
validation seam. It still rejects duplicate texts, body echoes and invalid saved
responses. Fresh dispatch does not reinterpret those responses as catalog refs.

The integration caller must authenticate current views before calling the
selector, with exactly the canonical section's local refs in current order.
Arbitrary untrusted caller strings are not authentication. Existing canonical
typed field validation and independent mandatory correlation retention remain
intake responsibilities, implemented before emission; the renderer does not
authorize field values.

## Membership and canonical facts

For each selected phrase, scan every current source view and retain all matching
record refs in original order. Count a matching record once even when its message
contains multiple occurrences. Derive `exact_metrics` using the existing
canonical record-metrics implementation. Derive `observed_window` with first and
last member timestamps and their elapsed span in milliseconds. The stored
observation contains only the selector, complete `event_refs`, these metrics,
and this observed window. It contains no full source-view sidecar.

The canonical summary emits an attributed JSON-quoted phrase and its retained-event
occurrence count, followed by short overlap and omission notices. Full member
facts, whole-section facts and exact typed fields remain structured in evidence
and the embedding recipe; fields do not affect summary size. Overlapping
memberships must not be summed. Source wording, including numbers, number words and
causal assertions, is quoted rather than adopted as application-established
fact. Reported response statuses do not establish impact or recovery. Measured
event work is distinct from observed timestamp span; missing samples are unknown
rather than zero measurements. Empty selection has an explicit unavailable-count
notice. Every rendering includes a fixed omission notice.

## Privacy and provenance

The caller supplies canonical, producer and projection body exclusions for every
current record. The validator checks all exclusions, not only nominated
witnesses. It rejects a phrase containing a complete protected body and rejects
selected fragments whose union of all occurrences covers a protected body.
Repeated and overlapping spans participate in that union. Once any span matches,
uncovered whitespace (`str.isspace`) and Unicode punctuation (category `P*`)
are conservatively treated as reconstructible separators, including interior
spaces, tabs, newlines and punctuation. Other uncovered characters, including
letters, numbers, symbols and combining marks, remain content-bearing. This rule
is for privacy only: substring matching, occurrence membership and retained
phrase bytes are never normalized. Exterior whitespace
does not evade the whole-body rule. Very short bodies can therefore have no safe
phrase; that does not permit relaxing privacy or inventing a semantic summary.

After all packed sections have selected phrases, their complete union is checked
against every protected body from the entire current batch before any compact
batch is returned. A late invalid response or cross-chunk reconstruction rejects
the whole result. Authenticated producer/projection views are used only with
exact current canonical bindings; direct/legacy/stale origins protect the
available canonical body without inventing missing producer provenance.

Source views and exclusions are transient. Durable validation recomputes member
metrics and windows and checks member ordering, uniqueness and witness inclusion.
It cannot prove substring membership again after disposal. The rendering says
source membership was checked before disposal;
it does not claim exhaustive classification or recovered source text.

Durable dispatch requires exact equality with canonical rendered text and binds
all v3 evidence, the render version and summary in immutable pattern/chunk IDs. Reference changes that
preserve identical metrics, or changed phrase text after source disposal, require
immutable identity checks; recomputed metrics alone cannot detect them.

## Durable and embedding dispatch

The v3 evidence object has exactly `version`, `render_version`, `records`,
`important_fields`, and `source_observations`. It accepts no `uncertainties` or
free-prose property. Records use the existing complete compact metadata shape;
selected fields retain their exact typed values and ordered event refs. Durable
validation recomputes whole-section and member facts, checks the canonical
summary and verifies both identities. Unknown renderer versions fail closed.
The existing 1,600-character summary and 16,000-character / 64,000-byte evidence
limits remain unchanged. The renderer also requires the canonical JSON string
envelope of the summary, including quotes and escaping, to fit 1,600 characters.
Four 120-character ordinary Unicode phrases fit independently of the selected
fields or up to 500 membership refs. Maximal escaped phrase sets may exceed this
bound and fail closed with `source_observation_summary_budget_exceeded`; phrases
are never shortened or silently dropped. Packing must reserve the complete
accepted summary cap and the independent full evidence/embedding bounds before
generation. Preflight uses the same instruction, safety suffix, payload and
schema as execution, measured independently in characters and UTF-8 bytes.
It reserves full record metadata, up to four expanded observations (witness,
all possible member refs, source-substring escaping, subset counts/status maps,
float measurements and timestamp windows), and required fields plus the largest
permissible optional choices to the remaining combined 64 slots. Character and
byte maxima may use different choices. The embedding reservation separately
accounts for the accepted summary cap, repeated phrase text, subset/whole facts,
semantic fields and fixed notices. These bounds do not promise provider token fit.
All packed sections are preflighted before any generation; an impossible single
fails explicitly. Final output/evidence/embedding serialized limits are checked
again without removing content. Catalog count overflow rejects instead of
silently trimming optional fields. Nothing is truncated.

V3 embedding text is canonical sorted compact Unicode JSON with exactly:
`evidence_version`, `render_version`, `embedding_version`, `summary`,
`source_observations`, `whole_section_facts`, `important_fields`, and
`measurement_notice`. The declared recipe is `source-observation-embedding-v1`.
The summary is the bounded canonical attributed rendering. Each structured
observation contains its exact `text`, `exact_metrics`, and `observed_window`;
whole-section facts contain the complete metrics and observed window. Selected
fields retain exact typed `key` and `value`. Witness/member refs and field refs
remain in durable evidence but are omitted from semantic transport. The fixed
measurement notice distinguishes reported statuses, unknown measurements,
summed measured work and observed timestamp span. The existing embedding
provider, model/query prefixes and spec remain unchanged. V1/v2 dispatch keeps
its original byte recipe and IDs. The inactive foundation renderer v1 has been
replaced before fresh v3 emission; this does not authorize converting any
historical durable representation.

Projection recovery still authorizes only v1-to-v2 transport changes. V3 input
is a no-op for that recovery; changing v1/v2 semantics to v3 is rejected. This
is not an authorization to accept prepared/pending source-aware reprocessing.

## Compatibility and active consumers

No historical v1/v2 source-aware migration is possible after source disposal.
Historical IDs, hashes, embedding recipes, inspection text, vectors and replay
semantics remain unchanged. Already prepared compact batches retain their version;
projection-only recovery cannot promote them to v3. Existing job lease, payload
hash and publication fencing remain in place.

`active_compact_context(chunk, cell)` validates the complete immutable compact
representation before deriving a half-open UTC query view. Original event keys,
IDs and local refs are preserved. Field memberships and authenticated phrase
memberships are intersected with retained records; empty fields/observations
are omitted and exact facts and attributed counts are recomputed. Sparse refs
are never passed to dense durable validators. If a stored phrase witness falls
outside the cell, a surviving authenticated member becomes `record_ref` and
`origin_record_ref` identifies the stored witness. This is membership provenance,
not recovery of discarded source text. Empty record views cannot become positive
query evidence. Records and fields remain usable when no phrases survive.

The same projected ordering supplies literal eligibility before global anchors,
fair admission and classification; literal indexes address that ordering, not
original ref numbers. Historical v1/v2 views retain exact in-window facts and the
source-discard notice, excluding unchecked summary/uncertainty prose. Classifier
transport retains the existing reversible columnar record codec and all limits.

Compact active evidence rows are addressed by `(cell_id, original_chunk_id)`.
Each row keeps canonical `id`/`chunk_id`, identity, bucket/window and pattern
metadata, while `query_window`, `facts_scope`, scoped first/last timestamps,
metrics, fields, observations and records explicitly describe that cell. Two
cells may therefore have rows with the same canonical citation ID and different
facts. Consumers must not deduplicate these rows by `id` alone. `citations` and
`cited_evidence_ids` remain canonical and unique; each citation lists its cells.
Findings, previews, readable answers and agent context use these scoped rows.
Native copied snapshots retain the cell binding and scoped facts; comparison
panels group by the explicit cell before considering canonical citation IDs.
Full stored artifacts remain available through inspection, without mutation.

Compact selected totals deduplicate canonical event keys within each cell and
reject conflicting retained metadata. Other representations retain their
whole-chunk boundary exclusion policy. Comparisons require matching source and
service scopes, equal duration and disjoint windows; even compatible selected
counts are not complete-population or causal comparisons. Historical embedding
recipes/vectors, positive-only classification, source fairness, all remap caps
and the absolute deadline remain unchanged.

Full deterministic mock migration and suite/native UI contract verification are
required for completion. Actual model, native intake, 1200/100K fidelity,
build/artifact/install and release acceptance remain separately owned gates.

## Joint checked-source lexical candidate admission

An actual hit in the existing bounded backend lexical pool can also pass the
rank gate when one current, validated, in-window v3 source quotation contains
all non-scaffolding query tokens. At least two distinct tokens must be ASCII
alphabetic words of three or more letters, excluding context identity/service
labels and identifier-like tokens. Matching uses whole casefolded word tokens,
keeping internal identifier punctuation; there is no stemming, synonym expansion,
partial-word matching, or stitching across quotations or records.

The closed English scaffolding list in `retriever.py` excludes grammatical and
question framing words, generic log/evidence/measurement vocabulary, and temporal
and negative operators. Those operators remain verbatim in the classifier's
question. Every other token, including any additional topic, must occur in the
same quote. Context labels and numeric/identifier tokens cannot supply the two
meaningful words. Renderer prose, counts, notices, hashes, fields and historical
unchecked summaries supply no quotation support. Other summary representations
retain their existing behavior; they have no new authenticated-quotation bypass.

This is candidate admission only. The cosine floor remains 0.5, and existing
exact-field/strong-identifier routes and subsequent literal eligibility are
unchanged. Same-field OR, different-field AND, absent-ID exclusion and fair
service/source and literal-witness ordering still apply before caps. Native
limits remain 6 candidates/cell, 3 selected/cell, 8 globally, 8 classification
calls and the shared absolute 60-second deadline. All transport/output limits
and strict independent positive-label/confidence requirements remain unchanged;
negative, invalid, cancelled or timed-out classification supplies no fallback.

## Active catalog transport and preflight

`compact_summary_batch` builds one call-local catalog from all authenticated
current views and canonical/producer/projection exclusions before packing.
Packing, all preflights, every fresh request and the strict known-ref decoder
receive that same catalog. All resulting singletons must pass before generation.
Only capacity failures permit splitting; binding errors abort. Catalog failures
have body-free error categories. No raw sidecar is durable. New chunks record
`selector_protocol:source-catalog-selector-v1` in loss notes; historical evidence,
rendering, hashes, embedding recipes and replay are unchanged.

Proposal grammar enumerates exact one-to-six-word Unicode windows at every
position, bounded to 120 characters. A word ending in `()` retains that exact
operation punctuation. Identical text has one physical ref and full exact
membership. At most 65,536 proposals and 4,096 catalog texts are allowed; overflow
rejects rather than trims. The complete global union still passes the unchanged
all-body/all-occurrence privacy predicate, so every selection subset stays safe.
The final atomic selected-union guard remains an independent defense.

Gap planning uses this generic hierarchy before phrase admission:

- Bounded parsed JSON key/leaf/object gap alternatives, eligible only when leaves
  occur in the complete typed field catalog. Exact original leaf-content spans
  supply structured proposals; decoding checks eligibility and never constructs
  a quote absent from source. Structured-value coverage precedes generic pattern
  coverage, so an unrelated prefix cannot mask lost useful values. Parsing has
  independent 65,536-character, 4,096-token and depth32 bounds; exhaustion rejects.
  Structurally identifier-shaped tokens are the next preference. No alternative
  removes original records, fields or complete model input.
- Existing case-insensitive connective gaps (`a an the and or but as at by for
  from in into of on to with without after before during through while`) when the
  body also contains content words. Negation `not` and `no` remains content.
- Otherwise, lexical positions containing terms repeated across the most distinct
  source patterns. Positions are ranked by corpus multiword-window loss, remaining
  contiguous windows and a declared positional tie: preserve the prefix for a
  single pattern, the suffix for multiple patterns.

At most four global assignments are evaluated. Their objective, in order, is
covered patterns, retained two/three-word windows across distinct patterns, rare
window coverage, then stable plan order. Each assignment is checked against all
body variants, including nonwitness occurrences and matches inside words. Hashes
only order proposals after gap planning. This bounded search is neither a
semantic classifier nor a proof of global infeasibility. Tiny whole-body anchors
can be impossible; uncovered individually proposable patterns fail with a
coverage conflict. Truly unproposable records receive explicit omissions and
remain complete model input.

The table repeats no object keys and presents exact readable text, with no offset
arithmetic. Reservations measure this actual table, complete instruction, safety
suffix, payload and strict known-ID schema. Character and UTF-8 limits remain
unchanged. Expansion reserves four distinct observations and four incoming refs
including duplicates, complete metadata, mandatory plus largest optional fields
within the combined 64-field limit, and the serialized 1600-character rendering
cap. Impossible singletons reject before generation; feasible field-heavy inputs
retain all mandatory and optional catalog entries. The render cap remains a
final acceptance predicate, with no shortening or repair.

This is active offline implementation, not native acceptance. Focused protocol,
source membership, privacy, scope, lexical and mocked persistence tests exercise
the route. The completed delegated full regression and mock migration pass;
independent review of the active route and structured-source correction passes. Actual model selection, frozen native 1200/100K, installed Chrome,
full token fit and deadline acceptance remain root-owned gates.
