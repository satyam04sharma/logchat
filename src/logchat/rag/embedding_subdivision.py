"""Lossless v3 record subdivision, separate from every embedding recipe.

Inputs must already have passed complete source/privacy validation at intake.
This operation proves inheritance; it never re-authenticates discarded bodies.
Physical admission owns a bounded counter scope; enqueue remains a caller action.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from datetime import datetime
import hashlib
import json

from .contracts import MAX_CHUNKS_PER_BATCH, MAX_EVENTS_PER_BATCH, MAX_SUMMARY_CHARS, EmbeddingSpec, PreparedBatch
from .scheduler import _encode
from .sections import (
    COMPACT_EVIDENCE_V3, _compact_id, _compact_pattern_id, _json,
    embedding_text, record_metrics, validate_compact_batch, validate_compact_chunk,
)
from .source_observations import _facts, render_observations

if TYPE_CHECKING:
    from pipeline.native_tokenizer.ollama import EmbeddingTokenCount
    from pipeline.token_budget import GenerationTokenIdentity

OPERATION_VERSION = "v3-embedding-record-subdivision-v1"


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_payload(batch, chunk):
    # Use precisely the scheduler's durable serialization, including typed JSON.
    return _json(json.loads(_encode(replace(batch, chunks=(chunk,))))["chunks"][0])


@dataclass(frozen=True, slots=True)
class LeafLineage:
    parent_index: int
    parent_chunk_id: str
    parent_sha256: str
    child_chunk_id: str
    child_sha256: str
    # Half-open original record interval; child eN maps to parent e(start + N).
    record_start: int
    record_end: int


@dataclass(frozen=True, slots=True)
class SubdivisionLineage:
    operation_version: str
    original_payload_sha256: str
    final_payload_sha256: str
    leaves: tuple[LeafLineage, ...]


def record_subset(parent, start, end):
    """Construct one nonempty contiguous inherited subset with dense local refs.

    A whole interval returns the exact original object, including legacy bytes.
    Only v3 can be changed. Witnesses outside the subset move to the first
    inherited matching member; no query-only origin_record_ref is persisted.
    """
    evidence = validate_compact_chunk(parent)
    original = evidence["records"]
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(original)):
        raise ValueError("subdivision_invalid_interval")
    if start == 0 and end == len(original):
        return parent
    if evidence["version"] != COMPACT_EVIDENCE_V3:
        raise ValueError("subdivision_requires_v3")
    remap = {row["ref"]: f"e{i}" for i, row in enumerate(original[start:end])}
    records = [{**row, "ref": remap[row["ref"]]} for row in original[start:end]]
    fields = []
    for field in evidence["important_fields"]:
        members = [remap[ref] for ref in field["event_refs"] if ref in remap]
        if members:
            fields.append({**field, "event_refs": members})
    observations = []
    lookup = {row["ref"]: row for row in records}
    for observation in evidence["source_observations"]:
        inherited = [ref for ref in observation["event_refs"] if ref in remap]
        if not inherited:
            continue
        members = [remap[ref] for ref in inherited]
        witness = observation["record_ref"]
        observations.append({"text": observation["text"], "event_refs": members,
            "record_ref": remap[witness if witness in remap else inherited[0]],
            **_facts([lookup[ref] for ref in members])})
    evidence = {**evidence, "records": records, "important_fields": fields,
                "source_observations": observations}
    summary = render_observations(observations, records, fields)
    stamps = [datetime.fromisoformat(row["timestamp"]) for row in records]
    child = replace(parent, compact_evidence=_json(evidence), summary=summary,
                    metrics=record_metrics(records), first_event_at=min(stamps),
                    last_event_at=max(stamps), pattern_id=_compact_pattern_id(evidence, summary))
    child = replace(child, chunk_id=_compact_id(child, child.compact_evidence, summary, child.summary_model))
    validate_compact_chunk(child)
    return child


def _bounded_batch(batch):
    # Reconstruct to enforce dataclass caps even for mutated caller-owned data.
    replace(batch)
    if len(batch.chunks) > MAX_CHUNKS_PER_BATCH or len(batch.event_keys) > MAX_EVENTS_PER_BATCH:
        raise ValueError("subdivision_batch_limit")
    payload = _encode(batch)
    if len(payload.encode("utf-8")) > 3_000_000:
        raise ValueError("subdivision_payload_limit")
    validate_compact_batch(batch)
    for chunk in batch.chunks:
        text = embedding_text(chunk)
        if len(text) > MAX_SUMMARY_CHARS or len(text.encode("utf-8")) > 4 * MAX_SUMMARY_CHARS:
            raise ValueError("subdivision_embedding_limit")
    return payload


def validate_subdivision(original: PreparedBatch, final: PreparedBatch) -> SubdivisionLineage:
    """Prove exact ordered parent→leaf conservation and return bounded lineage.

    Mapping is derived from records, never accepted from a caller's allowlist.
    Reconstructing each expected interval binds all fields, quotes, memberships,
    witnesses, facts, typed metadata, scope, model and loss notes simultaneously.
    Batch metadata and scheduler payload hashes are bound independently.
    """
    before, after = _bounded_batch(original), _bounded_batch(final)
    if _encode(replace(final, chunks=original.chunks)) != before:
        raise ValueError("subdivision_batch_mismatch")
    leaves, cursor = [], 0
    for parent_index, parent in enumerate(original.chunks):
        evidence = validate_compact_chunk(parent)
        count, offset = len(evidence["records"]), 0
        parent_payload = _chunk_payload(original, parent)
        while offset < count:
            if cursor >= len(final.chunks):
                raise ValueError("subdivision_missing_leaf")
            child = final.chunks[cursor]
            child_count = len(validate_compact_chunk(child)["records"])
            end = offset + child_count
            if end > count:
                raise ValueError("subdivision_parent_boundary")
            expected = record_subset(parent, offset, end)
            child_payload = _chunk_payload(final, child)
            if child_payload != _chunk_payload(original, expected):
                raise ValueError("subdivision_conservation_mismatch")
            leaves.append(LeafLineage(parent_index, parent.chunk_id, _digest(parent_payload),
                                     child.chunk_id, _digest(child_payload), offset, end))
            offset, cursor = end, cursor + 1
    if cursor != len(final.chunks):
        raise ValueError("subdivision_extra_leaf")
    return SubdivisionLineage(OPERATION_VERSION, _digest(before), _digest(after), tuple(leaves))


@dataclass(frozen=True, slots=True)
class DocumentAdmission:
    chunk_id: str
    input_sha256: str
    input_bytes: int
    physical_tokens: int
    # The counter's complete singleton request, including model and truncate.
    request_sha256: str


@dataclass(frozen=True, slots=True)
class RequestAdmission:
    first_document: int
    document_count: int
    proof: EmbeddingTokenCount


@dataclass(frozen=True, slots=True)
class EmbeddingSubdivisionPlan:
    batch: PreparedBatch
    lineage: SubdivisionLineage
    spec: EmbeddingSpec
    identity: GenerationTokenIdentity
    documents: tuple[DocumentAdmission, ...]
    requests: tuple[RequestAdmission, ...]
    physical_flags: tuple[bool, bool] = (True, True)
    context_tokens: int = 2048
    prefix_version: str = 'prefix-v1'


def _check_proof(proof, request, spec, identity):
    from pipeline.native_tokenizer.ollama import EmbeddingTokenCount, canonical
    from pipeline.token_budget import GenerationTokenIdentity
    if (not isinstance(proof, EmbeddingTokenCount)
            or not isinstance(proof.identity, GenerationTokenIdentity)
            or proof.identity != identity
            or canonical(identity.model) != canonical(spec.model)
            or spec.revision not in {identity.model_digest + ':prefix-v1',
                                     'sha256:' + identity.model_digest + ':prefix-v1'}
            or identity.runtime_version != '0.34.4'
            or identity.renderer_digest != _digest('prefix-v1')
            or proof.request_sha256 != hashlib.sha256(request).hexdigest()
            or type(proof.context_tokens) is not int or proof.context_tokens != 2048
            or type(proof.dimensions) is not int or proof.dimensions != 768
            or proof.prefix_version != 'prefix-v1'):
        raise ValueError('subdivision_token_proof_mismatch')
    body = json.loads(request)
    for counts in (proof.physical_tokens, proof.frontend_tokens):
        if (type(counts) is not tuple or len(counts) != len(body['input'])
                or any(type(n) is not int or n <= 0 for n in counts)):
            raise ValueError('subdivision_token_proof_mismatch')
    return proof.fits


async def plan_embedding_subdivision(original, provider, models, *, deadline):
    """Plan a fresh validated group, closing its one vocabulary before return.

    The caller must have authenticated the complete selected batch against raw
    sources. No queue writes, generation, embedding, or historical recovery occur
    here. The returned manifest is the observer seam, not durable payload fields.
    Candidate counts use singleton requests; the final <=16-document requests
    are also proved in the same owner (including cache hits), so their hashes
    match the actual wire batches rather than a concatenation approximation.
    """
    import asyncio
    from logchat.rag.embeddings import MAX_TEXTS, OllamaEmbeddingProvider
    from pipeline.model_transport import model_json
    from pipeline.native_tokenizer.ollama import canonical

    _bounded_batch(original)
    spec = provider.spec
    if (not isinstance(provider, OllamaEmbeddingProvider) or provider.preserve_content is not True
            or spec.provider != 'ollama' or canonical(spec.model) != 'nomic-embed-text:latest'
            or spec.dimensions != 768 or spec.distance != 'cosine'):
        raise ValueError('subdivision_provider_contract')
    binding = (provider.base_url, provider.transport, provider.preserve_content, spec,
               models.base_url, models.transport, models.embedding_model)

    def check():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError('subdivision_deadline')
        if ((provider.base_url, provider.transport, provider.preserve_content, provider.spec,
             models.base_url, models.transport, models.embedding_model) != binding
                or provider.base_url != models.base_url
                or provider.transport is not binding[1]
                or models.transport is not binding[5]
                or provider.transport is not models.transport
                or provider.spec.model != models.embedding_model):
            raise ValueError('subdivision_provider_drift')

    leaves, documents, requests = [], [], []
    identity = None
    async with asyncio.timeout_at(deadline):
        check()
        async with models.embedding_token_session(deadline=deadline) as counter:
            async def count(chunks):
                nonlocal identity
                check()
                body = provider.request_body([embedding_text(c) for c in chunks])
                request = model_json(body).encode('utf-8')
                proof = await counter.count(request)
                check()
                if identity is None:
                    identity = counter.identity
                fits = _check_proof(proof, request, spec, identity)
                if counter.identity != identity:
                    raise ValueError('subdivision_counter_drift')
                return body, proof, fits

            # A stack keeps midpoint order without assuming smaller inputs have
            # smaller token counts. Rebuild every interval from the ORIGINAL
            # parent, preserving the original witness when it survives.
            for parent in original.chunks:
                n = len(validate_compact_chunk(parent)['records'])
                pending = [(0, n)]
                while pending:
                    check()
                    start, end = pending.pop()
                    child = record_subset(parent, start, end)
                    body, proof, fits = await count((child,))
                    if not fits:
                        if validate_compact_chunk(parent)['version'] != COMPACT_EVIDENCE_V3:
                            raise ValueError('subdivision_requires_v3')
                        if end - start == 1:
                            raise ValueError('subdivision_impossible_singleton')
                        middle = (start + end) // 2
                        pending.extend(((middle, end), (start, middle)))
                        continue
                    leaves.append(child)
                    if len(leaves) > MAX_CHUNKS_PER_BATCH:
                        raise ValueError('subdivision_batch_limit')
                    if len(_encode(replace(original, chunks=tuple(leaves))).encode('utf-8')) > 3_000_000:
                        raise ValueError('subdivision_payload_limit')
                    text = body['input'][0]
                    documents.append(DocumentAdmission(child.chunk_id, _digest(text),
                        len(text.encode('utf-8')), proof.physical_tokens[0], proof.request_sha256))
            final = replace(original, chunks=tuple(leaves))
            lineage = validate_subdivision(original, final)
            for offset in range(0, len(leaves), MAX_TEXTS):
                chunks = leaves[offset:offset + MAX_TEXTS]
                _, proof, fits = await count(chunks)
                expected = tuple(d.physical_tokens for d in documents[offset:offset + MAX_TEXTS])
                if not fits or proof.physical_tokens != expected:
                    raise ValueError('subdivision_final_proof_mismatch')
                requests.append(RequestAdmission(offset, len(chunks), proof))
            await counter.verify_provider()
            check()
            if counter.identity != identity:
                raise ValueError('subdivision_counter_drift')
        check()
        plan = EmbeddingSubdivisionPlan(final, lineage, spec, identity, tuple(documents), tuple(requests))
        validate_embedding_plan(original, plan, provider)
        check()
        return plan


def validate_embedding_plan(original, plan, provider):
    """Pure strict observer seam: original compact return → exact final requests.

    This verifies integrity, not authenticity of a caller-supplied tokenizer.
    Production obtains proofs only from its owned default token session.
    """
    from logchat.rag.embeddings import MAX_TEXTS
    from pipeline.model_transport import model_json
    if (plan.lineage != validate_subdivision(original, plan.batch)
            or plan.spec != provider.spec or plan.spec.dimensions != 768
            or plan.spec.provider != 'ollama' or provider.preserve_content is not True
            or plan.physical_flags != (True, True)
            or any(type(flag) is not bool for flag in plan.physical_flags)
            or type(plan.context_tokens) is not int or plan.context_tokens != 2048
            or plan.prefix_version != 'prefix-v1'
            or len(plan.documents) != len(plan.batch.chunks)
            or len(plan.requests) != (len(plan.documents) + MAX_TEXTS - 1) // MAX_TEXTS):
        raise ValueError('subdivision_plan_mismatch')
    for chunk, document in zip(plan.batch.chunks, plan.documents):
        body = provider.request_body([embedding_text(chunk)])
        text = body['input'][0]
        if (document.chunk_id != chunk.chunk_id or document.input_sha256 != _digest(text)
                or document.input_bytes != len(text.encode('utf-8'))
                or document.request_sha256 != _digest(model_json(body))
                or type(document.physical_tokens) is not int
                or not 0 < document.physical_tokens <= 2048):
            raise ValueError('subdivision_document_mismatch')
    for index, admission in enumerate(plan.requests):
        offset = index * MAX_TEXTS
        chunks = plan.batch.chunks[offset:offset + MAX_TEXTS]
        body = provider.request_body([embedding_text(chunk) for chunk in chunks])
        if (admission.first_document != offset or admission.document_count != len(chunks)
                or not _check_proof(admission.proof, model_json(body).encode(), plan.spec, plan.identity)
                or admission.proof.physical_tokens != tuple(
                    d.physical_tokens for d in plan.documents[offset:offset + MAX_TEXTS])):
            raise ValueError('subdivision_request_mismatch')
    return plan.lineage
