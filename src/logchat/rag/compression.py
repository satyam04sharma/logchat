"""Deterministic semantic templates; no event bodies or variable values retained.

This is syntax-based compression, not model understanding. Unlabelled static
prose can still contain sensitive information; callers should supply known
secrets and avoid logging private prose. All downstream consumers treat these
templates as untrusted evidence, never instructions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from pipeline.redaction import redact_text

COMPRESSION_VERSION = "semantic-template-v1"
MAX_TEMPLATE_CHARS = 2800
MAX_INPUT_CHARS = 12000
_INSTRUCTION = re.compile(
    r"(?is)(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|above|system|developer)"
    r"|(?:system|developer|assistant)\s*(?:prompt|message|instructions?\s*:|:)"
    r"|(?:reveal|print|exfiltrate|send|upload)\s+(?:the\s+)?(?:secret|password|token|credentials?)"
    r"|<\|(?:im_start|system|assistant)|\[/?INST\]|you\s+are\s+(?:now\s+)?(?:an?\s+)?(?:assistant|chatgpt)"
)
_ASSIGNMENT = re.compile(r'''(?P<key>[\w.-]{1,80})\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|\[[^\]\n]*\]|\{[^}\n]*\}|[^\s,;]+)''')
_QUOTED = re.compile(r'''"[^"\n]*"|'[^'\n]*'|`[^`\n]*`''')
_URL = re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|redis|s3)://\S+")
_PATH = re.compile(r"(?<!\w)(?:[A-Za-z]:\\|/|\.\.?/)[^\s,;]+")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b")
_OPAQUE = re.compile(r"(?<!\w)(?:[0-9a-fA-F]{16,}|[A-Za-z0-9_+/=-]{40,})(?!\w)")
_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)*(?:ms|s|mb|gb|%)?(?![\w])", re.I)
_ERROR_IDENTIFIER = re.compile(r"(?:[A-Z][A-Z0-9_]{2,63}|[A-Za-z][A-Za-z0-9]*(?:Error|Exception|Revoked|Expired|Denied))\Z")


@dataclass(frozen=True, slots=True)
class Template:
    text: str
    loss_notes: tuple[str, ...]


def safe_metadata(value: str, *, secrets=(), limit: int = 200) -> str:
    text = redact_text(value, secrets)
    if _INSTRUCTION.search(text):
        return "[instruction-like metadata suppressed]"
    # Metadata is supplied separately by adapters, never extracted from raw prose.
    return " ".join(text.split())[:limit]


def compress_message(message: str, *, secrets=()) -> Template:
    if not isinstance(message, str) or len(message) > MAX_INPUT_CHARS:
        raise ValueError("input_character_bound_exceeded")
    text = redact_text(message, secrets)
    notes = {"deterministic_template_not_causal_summary", "unlabelled_static_text_may_remain"}
    if text != message:
        notes.add("known_secrets_and_recognized_pii_redacted")
    if _INSTRUCTION.search(text):
        return Template("instruction-like payload suppressed", tuple(sorted(notes | {"instruction_payload_suppressed"})))

    def mask(pattern, replacement, label):
        nonlocal text
        text, count = pattern.subn(replacement, text)
        if count:
            notes.add(label)

    mask(_URL, " <url> ", "url_values_removed")
    def assignment(match):
        key = match["key"]
        value = re.split(r"[:=]", match[0], maxsplit=1)[1].strip().strip("\"'")
        if key.lower() in {"code", "error_code", "error", "exception", "error_type"} and _ERROR_IDENTIFIER.fullmatch(value):
            return f"{key} {value}"
        return f"{key}=<value>"

    mask(_ASSIGNMENT, assignment, "parameter_values_removed")
    mask(_QUOTED, " <quoted> ", "quoted_payloads_removed")
    mask(_UUID, " <id> ", "volatile_identifiers_removed")
    mask(_PATH, " <path> ", "path_values_removed")
    mask(_OPAQUE, " <opaque> ", "opaque_values_removed")
    mask(_NUMBER, " <number> ", "numeric_values_removed")
    # A canonical token sequence removes formatting and repeated boilerplate. It
    # preserves vocabulary/word order beyond any finite operational dictionary.
    tokens = re.findall(r"<[^<>\s]+>|\[REDACTED_[A-Z]+\]|[\w]+(?:[._-][\w]+)*", text, re.UNICODE)
    normalized = []
    for token in tokens:
        if len(token) > 80:
            token = "<long-token>"
            notes.add("long_tokens_removed")
        if not _ERROR_IDENTIFIER.fullmatch(token):
            token = token.lower()
        if normalized and normalized[-1] == token:
            notes.add("adjacent_repetition_collapsed")
            continue
        normalized.append(token)
    template = " ".join(normalized) or "content removed by privacy normalization"
    if len(template) > MAX_TEMPLATE_CHARS:
        # Never hide rare suffixes behind silent slicing.
        raise ValueError("semantic_template_budget_exceeded_reformat_required")
    return Template(template, tuple(sorted(notes)))
