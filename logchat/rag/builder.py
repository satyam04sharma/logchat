"""Bounded source-neutral preparation and all-or-nothing embedding."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

from logchat.rag.compression import COMPRESSION_VERSION, compress_message, safe_metadata
from logchat.rag.contracts import (
    MAX_CHUNKS_PER_BATCH, MAX_EVENTS_PER_BATCH, BuildResult, Coverage,
    EmbeddedChunk, ExactMetrics, PreparedBatch, SemanticChunk, StageProvenance,
    TimeWindow, utc,
)

BUCKET_SECONDS = 900
MAX_EVENTS_PER_CHUNK = 200
EMBEDDING_BATCH_SIZE = 16


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _scope(identity):
    return [identity.owner_id, identity.project_id, identity.environment_id, identity.source_id]


def event_key(identity, event_id: str) -> str:
    """Source-scoped opaque idempotence key, independent of evidence policy."""
    return _hash([_scope(identity), event_id])


def _metrics(events):
    durations = []
    statuses = Counter()
    for event in events:
        value = event.duration_ms
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 86400000:
                raise ValueError("invalid_event_duration")
            durations.append(float(value))
        status = event.request_status
        if status is not None:
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError("invalid_event_http_status")
            statuses[str(status)] += 1
    return ExactMetrics(len(events), len(durations), math.fsum(durations),
                        min(durations) if durations else None, max(durations) if durations else None,
                        dict(sorted(statuses.items())))


def prepare_batch(identity, events, window, *, secrets=()):
    """Prepare a bounded batch in memory; reject work that requires splitting.

    The caller asserts successful observation of ``window``. Events outside it
    are ignored. Duplicate IDs with conflicting content fail closed.
    """
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise ValueError("event_limit_exceeded_split_required")
    groups = defaultdict(list)
    seen = {}
    accepted = []
    for event in events:
        stamp = utc(event.ts)
        if not window.start <= stamp < window.end:
            continue
        if not isinstance(event.event_id, str) or not event.event_id or len(event.event_id) > 1000:
            raise ValueError("invalid_event_identity")
        key_for_event = event_key(identity, event.event_id)
        template = compress_message(event.message, secrets=secrets)
        service = safe_metadata(event.service, secrets=secrets) or "application"
        level = safe_metadata(event.level, secrets=secrets, limit=30) or "unknown"
        release = safe_metadata(event.release, secrets=secrets) if event.release else None
        metrics = _metrics([event])
        signature = (stamp, template, service, level, release, metrics.duration_sum_ms,
                     metrics.duration_count, tuple(metrics.status_counts.items()))
        if key_for_event in seen:
            if seen[key_for_event] != signature:
                raise ValueError("duplicate_event_identity_conflict")
            continue
        seen[key_for_event] = signature
        accepted.append(event)
        start = datetime.fromtimestamp(int(stamp.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS, tz=timezone.utc)
        start = max(start, window.start)
        # Calculate the unmodified bucket end when the job starts mid-bucket.
        bucket_end = datetime.fromtimestamp((int(stamp.timestamp()) // BUCKET_SECONDS + 1) * BUCKET_SECONDS, tz=timezone.utc)
        end = min(bucket_end, window.end)
        groups[(start, end, service, level, release or "", template.text)].append((key_for_event, event, template.loss_notes))

    all_keys = tuple(sorted(seen))
    batch_id = _hash([COMPRESSION_VERSION, _scope(identity), window.start.isoformat(), window.end.isoformat(), all_keys])
    chunks = []
    for key in sorted(groups):
        start, end, service, level, release, text = key
        rows = sorted(groups[key], key=lambda row: (utc(row[1].ts), row[0]))
        pattern_id = _hash([COMPRESSION_VERSION, text])
        for offset in range(0, len(rows), MAX_EVENTS_PER_CHUNK):
            part = rows[offset:offset + MAX_EVENTS_PER_CHUNK]
            members = [row[1] for row in part]
            notes = tuple(sorted({note for row in part for note in row[2]}))
            summary = f"Observed event template: {text}."
            chunk_id = _hash([batch_id, [start.isoformat(), end.isoformat(), service, level, release, pattern_id], [row[0] for row in part]])
            chunks.append(SemanticChunk(chunk_id, identity, TimeWindow(start, end), service, level,
                summary, _metrics(members), pattern_id, release or None,
                min(utc(e.ts) for e in members), max(utc(e.ts) for e in members), COMPRESSION_VERSION, notes))
            if len(chunks) > MAX_CHUNKS_PER_BATCH:
                raise ValueError("chunk_limit_exceeded_split_required")
    coverage = Coverage(identity, window, "complete" if accepted else "empty", _metrics(accepted))
    return PreparedBatch(batch_id, identity, window, tuple(chunks), (coverage,), all_keys)


def prepare_preserved_batch(identity, events, window):
    """Preserve exact event text/values for the explicit local-only policy.

    Intake is deterministic and transient. The compact path must finish local
    sectioning and compaction before enqueueing durable work. No redactor or
    message generalizer participates in this in-memory preparation.
    """
    from logchat.rag.sections import INTAKE_VERSION, make_chunk, validate_batch
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise ValueError("event_limit_exceeded_split_required")
    records, accepted = {}, []
    for event in events:
        stamp = utc(event.ts)
        if not window.start <= stamp < window.end:
            continue
        for name, limit in (("event_id", 1000), ("message", 12000), ("source", 200),
                            ("service", 200), ("level", 30), ("fingerprint", 1000)):
            value = getattr(event, name)
            if not isinstance(value, str) or not value or len(value) > limit:
                raise ValueError("preserved_event_field_bound_exceeded")
        if event.release is not None and (not isinstance(event.release, str) or len(event.release) > 200):
            raise ValueError("preserved_event_field_bound_exceeded")
        _metrics([event])
        key = event_key(identity, event.event_id)
        record = {"event_key": key, "event_id": event.event_id, "timestamp": stamp.isoformat(),
            "source": event.source, "service": event.service, "level": event.level,
            "release": event.release, "fingerprint": event.fingerprint,
            "message": event.message, "duration_ms": event.duration_ms, "request_status": event.request_status}
        if key in records:
            if records[key] != record:
                raise ValueError("duplicate_event_identity_conflict")
            continue
        records[key] = record
        accepted.append(event)
    keys = tuple(sorted(records))
    batch_id = _hash([INTAKE_VERSION, _scope(identity), window.start.isoformat(), window.end.isoformat(), keys])
    chunks = []
    for key in keys:
        record = records[key]
        stamp = datetime.fromisoformat(record["timestamp"])
        bucket_start = datetime.fromtimestamp(int(stamp.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS, tz=timezone.utc)
        chunk_window = TimeWindow(max(window.start, bucket_start), min(window.end, bucket_start + timedelta(seconds=BUCKET_SECONDS)))
        chunks.append(make_chunk(batch_id, identity, chunk_window, [record], INTAKE_VERSION))
        if len(chunks) > MAX_CHUNKS_PER_BATCH:
            raise ValueError("chunk_limit_exceeded_split_required")
    batch = PreparedBatch(batch_id, identity, window, tuple(chunks),
        (Coverage(identity, window, "complete" if accepted else "empty", _metrics(accepted)),), keys)
    validate_batch(batch, version=INTAKE_VERSION)
    return batch


async def embed_batch(batch, provider):
    """No publication is performed until every requested vector is validated."""
    if any(chunk.compression_version.startswith("local-") for chunk in batch.chunks):
        from pipeline.models import ModelUnavailable
        if getattr(provider, "local_only", False) is not True or getattr(provider, "preserve_content", False) is not True:
            raise ModelUnavailable("local_preserving_embedding_provider_required")
    spec = provider.spec
    embedded = []
    for offset in range(0, len(batch.chunks), EMBEDDING_BATCH_SIZE):
        chunks = batch.chunks[offset:offset + EMBEDDING_BATCH_SIZE]
        from logchat.rag.sections import embedding_text
        texts = [embedding_text(chunk) if chunk.compression_version.startswith("local-") else chunk.summary for chunk in chunks]
        vectors = await provider.embed(texts, purpose="document")
        if provider.spec != spec or len(vectors) != len(chunks):
            raise ValueError("embedding_batch_or_model_mismatch")
        embedded.extend(EmbeddedChunk(chunk, tuple(vector), spec) for chunk, vector in zip(chunks, vectors))
    versions = {chunk.compression_version for chunk in batch.chunks}
    implementation = next(iter(versions)) if len(versions) == 1 else (COMPRESSION_VERSION if not versions else "mixed_representations")
    section_models = {note.partition(":")[2] for chunk in batch.chunks for note in chunk.loss_notes if note.startswith("section_model:")}
    summary_models = {chunk.summary_model for chunk in batch.chunks if chunk.summary_model is not None}
    summarized = bool(summary_models)
    compact = implementation == "local-model-compact-v1"
    compression_models = summary_models if summarized else section_models
    provenance = [
        StageProvenance("compression", "ok" if batch.chunks else "empty", implementation,
                        model=next(iter(compression_models)) if len(compression_models) == 1 else None,
                        detail="abstractive_model_summary_selected_fields_original_messages_not_retained" if compact else
                            "model_selected_grounded_excerpts_full_records_retained" if summarized else
                            ("exact_local_records_model_partition_only" if implementation.startswith("local-") else "deterministic_generalized_templates_exact_metrics")),
        StageProvenance("embedding", "ok" if batch.chunks else "empty", spec.provider,
                        model=spec.model, revision=spec.revision,
                        detail="compact_summary_selected_fields_exact_metrics" if compact else
                            "compact_summary_and_exact_metrics_full_records_separate" if summarized else "complete_bounded_inputs"),
    ]
    if summarized:
        provenance.append(StageProvenance("summary", "ok", implementation if compact else "local-model-summary-v1",
            model=next(iter(summary_models)) if len(summary_models) == 1 else None,
            detail="selected_fields_checked_prose_not_verified_truth_source_detail_coverage_partial" if compact else
                "verbatim_quotes_and_all_reference_memberships_checked_excerpt_selection_not_exhaustive"))
    return BuildResult(batch.batch_id, tuple(embedded), batch.coverage, tuple(provenance))
