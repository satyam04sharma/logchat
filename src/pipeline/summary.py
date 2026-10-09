"""Deterministic full-batch facts and a deliberately lossy operational sketch."""
from collections import Counter
from datetime import timezone
import json
import re

from pipeline.redaction import TEXT_CHARACTER_LIMIT, sanitize


SUMMARY_PROVENANCE = "Deterministic full-batch compilation; no summary model request or model prose."


def encode_summary(payload, secrets=()):
    """Bound structured text by explicit map omission, never by cutting JSON."""
    payload = sanitize(payload, secrets)
    def encode():
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    text = encode()
    if len(text) <= TEXT_CHARACTER_LIMIT:
        return text
    projection = {
        "character_limit": TEXT_CHARACTER_LIMIT,
        "omitted_fact_maps": {},
        "exact_metrics": "Builder stores exact status counts and duration/event metrics separately in chunk and coverage fields.",
    }
    payload["text_projection"] = projection
    # Omit whole maps in descending serialized size; no partial counts masquerade
    # as complete facts. Fixed vocabulary and scalar facts are always retained.
    maps = sorted((key for key in ("status_counts", "service_counts", "severity_counts")
                   if key in payload["facts"]),
                  key=lambda key: (-len(json.dumps(payload["facts"][key], ensure_ascii=False)), key))
    for key in maps:
        counts = payload["facts"].pop(key)
        projection["omitted_fact_maps"][key] = {
            "distinct_values": len(counts), "event_count": sum(counts.values()),
        }
        text = encode()
        if len(text) <= TEXT_CHARACTER_LIMIT:
            return text
    raise ValueError("Summary scalar facts and vocabulary exceed the text character limit.")


# Same bounded operational vocabulary as native memory; no arbitrary message text.
OPERATIONAL_TERMS = tuple(dict.fromkeys((
    "timeout timed out slow latency duration connection connect disconnected database db sql cache redis "
    "http request response route endpoint server client worker queue job retry failed failure error exception "
    "warning unavailable refused reset memory cpu disk network auth permission denied rate limit startup shutdown "
    "broker celery task scheduler certificate ssl tls heartbeat healthcheck authentication traceback import module"
).split()))
TERM_PATTERNS = [(term, re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"))
                 for term in OPERATIONAL_TERMS]


def observed_terms(events, secrets=()):
    found = set()
    for event in events:
        message = sanitize(event.message, secrets).lower()
        for term, pattern in TERM_PATTERNS:
            if pattern.search(message):
                found.add(term)
    return [term for term in OPERATIONAL_TERMS if term in found]


def render_summary(events, terms, secrets=()):
    """Render observed facts only; terms indicate literal presence, never causes."""
    durations = [float(event.duration_ms) for event in events if event.duration_ms is not None]
    times = [event.ts.astimezone(timezone.utc) for event in events]
    facts = {
        "event_count": len(events),
        "observed_start": min(times).isoformat() if times else None,
        "observed_end": max(times).isoformat() if times else None,
        "service_counts": dict(sorted(Counter(sanitize(event.service, secrets) for event in events).items())),
        "severity_counts": dict(sorted(Counter(sanitize(event.level, secrets) for event in events).items())),
        "duration_count": len(durations), "duration_sum_ms": sum(durations),
        "duration_min_ms": min(durations) if durations else None,
        "duration_max_ms": max(durations) if durations else None,
        "status_counts": dict(sorted(Counter(str(event.request_status) for event in events
                                                if event.request_status is not None).items())),
    }
    return encode_summary({
        "facts": facts, "operational_terms": terms,
        "scope": "Full supplied batch; literal terms are a lossy fixed-vocabulary sketch, not causes or prevalence. No raw messages stored.",
        "provenance": SUMMARY_PROVENANCE,
    }, secrets)
