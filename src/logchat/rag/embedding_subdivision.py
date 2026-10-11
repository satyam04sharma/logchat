"""Lossless v3 record subdivision, separate from every embedding recipe.

Inputs must already have passed complete source/privacy validation at intake.
This operation proves inheritance; it never re-authenticates discarded bodies.
Physical admission and enqueue are deliberately outside this pure transform.
"""
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json

from .contracts import MAX_CHUNKS_PER_BATCH, MAX_EVENTS_PER_BATCH, MAX_SUMMARY_CHARS, PreparedBatch
from .scheduler import _encode
from .sections import (
    COMPACT_EVIDENCE_V3, _compact_id, _compact_pattern_id, _json,
    embedding_text, record_metrics, validate_compact_batch, validate_compact_chunk,
)
from .source_observations import _facts, render_observations

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
