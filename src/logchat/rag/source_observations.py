"""Bounded source selectors and deterministic facts for fresh compact evidence.

Source validation is transient. Durable validation checks stored bindings and
recomputed facts; it cannot reconstruct or authenticate discarded source text.
This module does not migrate historical evidence or invoke a model.
"""
from dataclasses import asdict
from datetime import datetime
import json
import unicodedata

from logchat.rag.contracts import MAX_MODEL_SUMMARY_CHARS

EVIDENCE_VERSION = "compact-evidence-v3"
RENDER_VERSION = "source-observation-render-v2"
EMBEDDING_VERSION = "source-observation-embedding-v1"
MAX_SERIALIZED_SUMMARY_CHARS = MAX_MODEL_SUMMARY_CHARS
MAX_PHRASES = 4
MAX_PHRASE_CHARS = 120
PROVENANCE = "Source membership checked before source disposal."
OMISSION = "Selected details only; omitted source details cannot be recovered."
OVERLAP = "Memberships may overlap; do not add counts."


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _lookup(records):
    refs = [row["ref"] for row in records]
    if refs != [f"e{i}" for i in range(len(records))] or not refs:
        raise ValueError("invalid_observation_records")
    return dict(zip(refs, records))


def _facts(members):
    from logchat.rag.sections import record_metrics
    stamps = [datetime.fromisoformat(row["timestamp"]) for row in members]
    first, last = min(stamps), max(stamps)
    return {"exact_metrics": asdict(record_metrics(members)), "observed_window": {
        "start": first.isoformat(), "end": last.isoformat(),
        "span_ms": (last - first).total_seconds() * 1000}}


def _phrase_shape(value):
    if (not isinstance(value, dict) or set(value) != {"record_ref", "text"}
            or not isinstance(value["record_ref"], str)
            or not isinstance(value["text"], str) or not value["text"].strip()
            or len(value["text"]) > MAX_PHRASE_CHARS):
        raise ValueError("invalid_source_phrase")


def _protect_bodies(phrases, bodies):
    # Union every occurrence, including repeated/overlapping spans, for every
    # protected body, not merely a nominated witness. Whitespace and Unicode
    # punctuation gaps are reconstructible separators for privacy only. Source
    # matching and retained phrase bytes remain exact; symbols, letters, numbers
    # and combining marks must actually be covered.
    for body in bodies:
        body = body.strip()
        if not body:
            continue
        covered = bytearray(len(body))
        for phrase in phrases:
            if body in phrase:
                raise ValueError("source_phrase_body_echo")
            offset = 0
            while (start := body.find(phrase, offset)) >= 0:
                covered[start:start + len(phrase)] = b"\x01" * len(phrase)
                offset = start + 1
        if any(covered) and all(
                hit or char.isspace() or unicodedata.category(char).startswith("P")
                for hit, char in zip(covered, body)):
            raise ValueError("source_phrase_body_reconstruction")


def select_observations(value, records, source_views, protected_bodies, field_refs, *, output_budget):
    """Validate the complete model response; return facts and selected field IDs.

The caller supplies already authenticated current source views and canonical,
producer and projection body exclusions keyed by the same local record refs.
No source text or exclusion sidecar is returned for durable storage.
"""
    lookup = _lookup(records)
    if set(source_views) != set(lookup) or set(protected_bodies) != set(lookup):
        raise ValueError("source_observation_scope_mismatch")
    if any(not isinstance(text, str) or not text for text in source_views.values()):
        raise ValueError("invalid_observation_source")
    if (not isinstance(value, dict) or set(value) != {"source_phrases", "important_fields"}
            or len(_json(value)) > output_budget):
        raise ValueError("invalid_source_observation_output")
    phrases, fields = value["source_phrases"], value["important_fields"]
    if not isinstance(phrases, list) or len(phrases) > MAX_PHRASES:
        raise ValueError("invalid_source_phrases")
    if (not isinstance(fields, list) or len(fields) > 64
            or any(not isinstance(ref, str) or ref not in field_refs for ref in fields)
            or len(set(fields)) != len(fields)):
        raise ValueError("invalid_source_observation_fields")
    texts, observations = set(), []
    for phrase in phrases:
        _phrase_shape(phrase)
        ref, text = phrase["record_ref"], phrase["text"]
        if ref not in lookup or text not in source_views[ref] or text in texts:
            raise ValueError("invalid_source_phrase_witness")
        texts.add(text)
        refs = [ref for ref in lookup if text in source_views[ref]]
        observations.append({**phrase, "event_refs": refs, **_facts([lookup[ref] for ref in refs])})
    bodies = []
    for ref in lookup:
        exclusions = protected_bodies[ref]
        if not isinstance(exclusions, (list, tuple, set)) or any(not isinstance(body, str) for body in exclusions):
            raise ValueError("invalid_observation_body_exclusions")
        bodies.extend([source_views[ref], *exclusions])
    _protect_bodies(texts, bodies)
    return observations, list(fields)


def validate_observations(observations, records):
    """Recompute durable facts without claiming to recheck discarded sources."""
    lookup, texts = _lookup(records), set()
    if not isinstance(observations, list) or len(observations) > MAX_PHRASES:
        raise ValueError("invalid_source_observations")
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {
                "record_ref", "text", "event_refs", "exact_metrics", "observed_window"}:
            raise ValueError("invalid_source_observation")
        _phrase_shape({key: observation[key] for key in ("record_ref", "text")})
        refs = observation["event_refs"]
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or ref not in lookup for ref in refs)
                or refs != [ref for ref in lookup if ref in refs]
                or observation["record_ref"] not in refs or observation["text"] in texts):
            raise ValueError("invalid_source_observation_membership")
        texts.add(observation["text"])
        facts = _facts([lookup[ref] for ref in refs])
        if _json(facts) != _json({key: observation[key] for key in facts}):
            raise ValueError("source_observation_fact_mismatch")
    return observations


def render_observations(observations, records, fields):
    """Bounded attributed occurrence counts; full facts stay in evidence.

    The JSON string envelope counts toward the existing summary cap, including
    its escaping. Reject rather than truncate any selected source phrase.
    Fields deliberately do not affect this rendering or its bound.
    """
    validate_observations(observations, records)
    return _render_checked_observations(observations)


def _render_checked_observations(observations):
    """Render already validated durable or derived memberships, without renumbering."""
    lines = [PROVENANCE]
    for observation in observations:
        lines.append(f'{len(observation["event_refs"])} retained events contain source phrase '
                     f'{_json(observation["text"])} (source quote).')
    if not observations:
        lines.append("No source phrases selected; source-derived operation counts are unavailable.")
    lines.extend([OVERLAP, OMISSION])
    summary = "\n".join(lines)
    if len(_json(summary)) > MAX_SERIALIZED_SUMMARY_CHARS:
        raise ValueError("source_observation_summary_budget_exceeded")
    return summary


def observation_embedding(observations, records, fields):
    """V3 semantic recipe: checked facts once, without local ref bookkeeping."""
    summary = render_observations(observations, records, fields)
    return {"evidence_version": EVIDENCE_VERSION, "render_version": RENDER_VERSION,
            "embedding_version": EMBEDDING_VERSION, "summary": summary,
            "source_observations": [
                {key: observation[key] for key in ("text", "exact_metrics", "observed_window")}
                for observation in observations],
            "whole_section_facts": _facts(records),
            "important_fields": [{"key": field["key"], "value": field["value"]} for field in fields],
            "measurement_notice": "Status counts are reported responses; missing measurements are unknown. "
                "duration_sum_ms is summed measured work, not elapsed time; observed_window.span_ms is timestamp span."}


def scoped_observations(evidence, retained_records):
    """Intersect authenticated durable membership, preserving original refs.

    The caller validates the immutable chunk, including its identity. Bind the
    derived records to that complete representation, never a dense substitute.
    A surviving member is a valid display witness; the stored witness remains
    separate provenance, not an assertion that its event is in scope.
    """
    original = evidence["records"]
    observations = validate_observations(evidence["source_observations"], original)
    refs = {row["ref"] for row in retained_records}
    expected = [row for row in original if row["ref"] in refs]
    if _json(expected) != _json(retained_records):
        raise ValueError("scoped_observation_record_mismatch")
    lookup = {row["ref"]: row for row in expected}
    projected = []
    for observation in observations:
        members = [ref for ref in observation["event_refs"] if ref in lookup]
        if not members:
            continue
        witness = observation["record_ref"]
        item = {"record_ref": witness if witness in members else members[0],
                "text": observation["text"], "event_refs": members,
                **_facts([lookup[ref] for ref in members])}
        if witness not in members:
            item["origin_record_ref"] = witness
        projected.append(item)
    return projected, _render_checked_observations(projected)
