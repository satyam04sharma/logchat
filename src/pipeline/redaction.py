"""The sole boundary before model calls; never log rejected input."""
import hashlib
import re
from dataclasses import replace

from pipeline.types import LogEvent

# Specific secrets first, then generic assignments and PII. Also redact model output.
PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", "[REDACTED_KEY]"),
    (r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]+", "[REDACTED_AUTH]"),
    (r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED_TOKEN]"),
    (r"(?i)\b(?:sk[-_]|gh[pousr]_|github_pat_|sntrys_|sb_secret_|AKIA)[A-Za-z0-9_-]{8,}", "[REDACTED_TOKEN]"),
    (r"(?i)([\"']?(?:password|passwd|secret|token|api[-_ ]?key|authorization|cookie|session[-_]?id)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[REDACTED_SECRET]"),
    (r"(?i)(https?://|postgres(?:ql)?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED_AUTH]@"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]"),
    (r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", "[REDACTED_IP]"),
    (r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F:]{0,39}(?![\w:])", "[REDACTED_IP]"),
    (r"(?i)([?&](?:email|token|key|secret|password|session|access_token)=)[^&\s]+", r"\1[REDACTED_SECRET]"),
]
COMPILED = [(re.compile(pattern), replacement) for pattern, replacement in PATTERNS]
TEXT_CHARACTER_LIMIT = 12000


def redact_text(value: str, secrets=()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED_SECRET]")
    for pattern, replacement in COMPILED:
        text = pattern.sub(replacement, text)
    return text[:TEXT_CHARACTER_LIMIT]


def redact_event(event: LogEvent, secrets=()) -> LogEvent:
    return replace(event, event_id=hashlib.sha256(event.event_id.encode()).hexdigest(),
                   message=redact_text(event.message, secrets), service=redact_text(event.service, secrets)[:200],
                   level=redact_text(event.level, secrets)[:30], source=redact_text(event.source, secrets)[:200],
                   fingerprint=hashlib.sha256(redact_text(event.fingerprint, secrets).encode()).hexdigest(),
                   release=redact_text(event.release, secrets)[:200] if event.release else None)


def sanitize(value, secrets=()):
    if isinstance(value,str): return redact_text(value, secrets)
    if isinstance(value,list): return [sanitize(item, secrets) for item in value]
    if isinstance(value,dict): return {key:sanitize(item, secrets) for key,item in value.items()}
    return value
