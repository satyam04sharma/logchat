# Reversible relevance record view

Relevance selection projects records only at the candidate model-payload boundary.
The `relevance-record-columns-v1` object carries ordered keys, typed shared values,
ordered remaining columns, and one complete row of column values per original
record. Shared values apply to every row. Optional key orders retain differing
source object orders. A list remains an ordinary record list, so literal source
fields named `codec`, `rows`, `shared`, or `ref` cannot collide with the framing.

Projection requires multiple records with identical key sets and is used only
when its compact UTF-8 JSON is shorter. Shared-value comparisons use exact JSON
spelling, preserving nested values, booleans versus integers, integers versus
floats, signed floating zero, null, Unicode, and pointer-like keys. Heterogeneous
key sets remain ordinary lists, preserving missing versus null. The inverse
regressions cross a JSON encode/decode boundary and compare original record bytes.

This view does not filter records, change literal matching indexes, candidate
references, whole-section metrics, source attribution, or the original candidate
used for citations. Durable records, identities, embeddings, and recovery codecs
are unchanged. A mock semantic relevance regression retains all 33 records.

The relevance budget and native local generation prompt use the same compact
UTF-8 JSON spelling. Instruction and complete judgement schema still count
toward the application character/byte budget. A mocked native transport regression
checks the actual submitted prompt, typed inverse, original citation binding,
and admission at the exact budget boundary versus rejection one byte or character
below it. Durable JSON, digests, embedding recipes, and output validation budgets
retain their existing spelling. Other stage budgets can remain conservative.

This slice establishes reversible record projection and compact submission, not
native token fit or actual model relevance quality. Byte savings are not token
counts. The application budget excludes provider-added system text and template
rendering; it is not a full-render-plus-output-reserve proof. The [native context guard](native-generation-context-guard.md) rejects
overfull input on the verified provider/backend combination; host-owned actual
acceptance proofs are still required. The requested external architecture review file was absent from this checkout at
implementation time; the supplied objective served as the bounded requirements.
