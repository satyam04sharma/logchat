"""Bounded serializable user-only checkpoints; persistence belongs to the host.

Lexical topic recall assists query construction, never supplies log evidence.
Explicit scope values override remembered values, including explicit nulls.
"""
from __future__ import annotations

from copy import deepcopy
from itertools import islice
import json
import re

from pipeline.redaction import redact_text

VERSION = "user-context-v1"
MAX_CHARS = 6000
MAX_RECENT = 12
MAX_CONSTRAINTS = 16
MAX_TOPICS = 64
MAX_OLDER_SCAN = 256
SCOPE_KEYS = {"environment_ids", "source_ids", "timezone", "start", "end", "compare_start", "compare_end", "service"}
STOP = set(("a an the and or to of in on for from with about how why what where when which "
    "is are was were be been did does do can could would should will have has had "
    "it they them that those this these their earlier previous again same back return "
    "logs log events event evidence show tell explain compare observed observations "
    "errors error failure failed failures duration durations latency latencies "
    "mean average measurements measured available hour hours day days week weeks "
    "month months today yesterday current last before after please").split())


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _terms(text):
    return list(dict.fromkeys(word[:80] for word in re.findall(r"[a-z][a-z0-9_]{2,}", text.lower()) if word not in STOP))[:24]


def _referring(text):
    return bool(re.search(r"\b(?:that|those|their|them|it|again|same|back to|return to)\b|\b(?:earlier|previous)\b(?!\s+(?:day|week|month|year))", text, re.I))


def _validate_scope(scope):
    if not isinstance(scope, dict) or set(scope) - SCOPE_KEYS:
        raise ValueError("invalid_user_context_scope")
    value = {}
    for key, item in scope.items():
        if key in {"environment_ids", "source_ids"}:
            if item is not None and (not isinstance(item, (list, tuple)) or len(item) > 64 or any(not isinstance(x, str) or not 1 <= len(x) <= 200 for x in item)):
                raise ValueError("invalid_user_context_scope")
            value[key] = list(dict.fromkeys(item)) if item is not None else None
        else:
            if item is not None and (not isinstance(item, str) or len(item) > 200):
                raise ValueError("invalid_user_context_scope")
            value[key] = redact_text(item) if item is not None else None
    if len(_json(value)) > 3000:
        raise ValueError("user_context_scope_budget_exceeded")
    return value


def empty_context(*, owner_id, project_id, conversation_id):
    if any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in (owner_id, project_id, conversation_id)):
        raise ValueError("user_context_identity_required")
    return {"version": VERSION, "owner_id": owner_id, "project_id": project_id,
        "conversation_id": conversation_id, "turn_count": 0, "scope": {}, "recent": [],
        "constraints": [], "topics": [], "loss": {"truncated_questions": 0, "evicted_recent": 0,
        "evicted_constraints": 0, "evicted_topics": 0}, "memory_is_evidence": False}


def _check(checkpoint):
    if (not isinstance(checkpoint, dict) or checkpoint.get("version") != VERSION
            or checkpoint.get("memory_is_evidence") is not False or len(_json(checkpoint)) > MAX_CHARS
            or type(checkpoint.get("turn_count")) is not int or checkpoint["turn_count"] < 0):
        raise ValueError("invalid_user_context_checkpoint")
    required = set(empty_context(owner_id="x", project_id="x", conversation_id="x"))
    if set(checkpoint) != required:
        raise ValueError("invalid_user_context_checkpoint")
    empty_context(owner_id=checkpoint["owner_id"], project_id=checkpoint["project_id"], conversation_id=checkpoint["conversation_id"])
    _validate_scope(checkpoint["scope"])
    for key, cap, size in (("recent", MAX_RECENT, 400), ("constraints", MAX_CONSTRAINTS, 200), ("topics", MAX_TOPICS, 80)):
        if not isinstance(checkpoint[key], list) or len(checkpoint[key]) > cap or any(not isinstance(item, str) or len(item) > size for item in checkpoint[key]):
            raise ValueError("invalid_user_context_checkpoint")
    if set(checkpoint["loss"]) != {"truncated_questions", "evicted_recent", "evicted_constraints", "evicted_topics"} or any(type(v) is not int or v < 0 for v in checkpoint["loss"].values()):
        raise ValueError("invalid_user_context_checkpoint")


def resolve_scope(checkpoint, explicit_scope):
    """Omitted keys inherit; an explicitly supplied null clears a prior value."""
    _check(checkpoint)
    return _validate_scope({**checkpoint["scope"], **_validate_scope(explicit_scope)})


def advance_user_context(checkpoint, question, effective_scope, *, role="user"):
    _check(checkpoint)
    if role != "user":
        raise ValueError("only_user_turns_may_advance_context")
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("user_question_bound_exceeded")
    question = redact_text(question)
    result = deepcopy(checkpoint)
    result["turn_count"] += 1
    result["scope"] = _validate_scope(effective_scope)
    snippet = question[:400]
    if len(question) > 400:
        result["loss"]["truncated_questions"] += 1
    result["recent"] = [old for old in result["recent"] if old != snippet] + [snippet]
    constraints = [match.strip()[:200] for match in re.findall(r"[^.!?]*(?:\bdo not\b|\bnever\b|\bonly\b|\bmust\b|\bkeep\b)[^.!?]*", question, re.I)]
    result["constraints"] = list(dict.fromkeys(result["constraints"] + constraints))
    result["topics"] = list(dict.fromkeys(result["topics"] + _terms(question)))
    for key, limit in (("recent", MAX_RECENT), ("constraints", MAX_CONSTRAINTS), ("topics", MAX_TOPICS)):
        while len(result[key]) > limit:
            result[key].pop(0); result["loss"]["evicted_" + key] += 1
    # Scope is never truncated. Exact user constraints outrank recent snippets.
    for key in ("recent", "topics", "constraints"):
        while len(_json(result)) > MAX_CHARS and result[key]:
            result[key].pop(0); result["loss"]["evicted_" + key] += 1
    if len(_json(result)) > MAX_CHARS:
        raise ValueError("user_context_budget_exceeded")
    return result


def recall_user_context(checkpoint, question, *, older_user_turns=()):
    """Read bounded user snippets from a host-supplied newest-first history page."""
    _check(checkpoint)
    if not isinstance(question, str) or len(question) > 4000:
        raise ValueError("user_question_bound_exceeded")
    question = redact_text(question)
    rows = tuple(islice(older_user_turns, MAX_OLDER_SCAN + 1))
    older_capped = len(rows) > MAX_OLDER_SCAN
    # Explicit role filtering prevents accidental assistant history promotion.
    older = [redact_text(row["content"])[:400] for row in rows[:MAX_OLDER_SCAN]
             if isinstance(row, dict) and row.get("role") == "user"
             and isinstance(row.get("content"), str) and len(row["content"]) <= 4000]
    snippets = list(dict.fromkeys(list(reversed(checkpoint["recent"])) + older))
    recalled = []
    if _referring(question):
        terms = set(_terms(question))
        if terms:
            scored = [(len(terms.intersection(_terms(text))), index, text) for index, text in enumerate(snippets)]
            recalled = [text for score, _, text in sorted(scored, key=lambda item: (-item[0], item[1])) if score][:4]
        else:
            recalled = next(([text] for text in snippets if _terms(text)), [])
    return {"scope": deepcopy(checkpoint["scope"]), "constraints": checkpoint["constraints"].copy(),
            "recent": checkpoint["recent"].copy(), "recalled": recalled,
            "metadata": {"strategy": VERSION + "_user_only_lexical_recall", "turn_count": checkpoint["turn_count"],
                "memory_is_evidence": False, "assistant_content_used": False, "older_page_capped": older_capped,
                "checkpoint_chars": len(_json(checkpoint)), "max_checkpoint_chars": MAX_CHARS,
                "loss": deepcopy(checkpoint["loss"]),
                "limits": "Bounded user-only lexical topic recall is lossy, not semantic conversation memory. Persist the checkpoint and transcript separately from log evidence."}}
