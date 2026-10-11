"""Transient, globally safe proposals for the catalog selector transport.

These helpers do not authenticate arbitrary strings: the intake caller must
supply its already authenticated current views and complete body exclusions.
Proposal coverage is not semantic/model-selected coverage. Historical selectors
and durable v3 rendering are deliberately unchanged.
"""
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import re
import json

from logchat.rag.source_observations import (
    MAX_PHRASE_CHARS, MAX_PHRASES, _json, _lookup, _protect_bodies,
    select_observations,
)

SELECTOR_PROTOCOL = "source-catalog-selector-v2"
MAX_PROPOSALS = 65536
MAX_CATALOG_PHRASES = 4096
MAX_PROPOSAL_WORDS = 6
# A deliberately small English function-word grammar, not an operation or
# service vocabulary. Negation is content: never reserve "not"/"no" as gaps.
CONNECTIVE_GAPS = frozenset("a an the and or but as at by for from in into of on to with without after before during through while".split())


MAX_GAP_PLANS = 4


# Independent bounds on JSON offset parsing, including malformed candidates.
MAX_JSON_SCAN_CHARS = 65536
MAX_JSON_TOKENS = 4096
MAX_JSON_DEPTH = 32
_JSON_TOKEN = re.compile(
    r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
    r'|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
    r'|true|false|null|[{}\[\],:]')
_JSON_SPACE = re.compile(r'[ \t\r\n]*')


def _structured_spans(body, typed_values):
    """Parse bounded JSON tokens; offsets always address original source text.

    Decoding is only for semantic eligibility, never for quote construction.
    Arrays are typed atomic values (as in the field catalog), but their scalar
    tokens remain available as exact source spans. Keys at every depth count.
    """
    if '{' not in body:
        return ()
    if len(body) > MAX_JSON_SCAN_CHARS:
        raise ValueError("source_catalog_json_budget_exceeded")
    cursor, tokens_left, objects = 0, MAX_JSON_TOKENS, []
    for _ in range(64):
        start = body.find('{', cursor)
        if start < 0:
            break
        offset, stack, keys, leaves = start, [], [], []
        while offset < len(body):
            offset = _JSON_SPACE.match(body, offset).end()
            token = _JSON_TOKEN.match(body, offset)
            if token is None:
                break
            tokens_left -= 1
            if tokens_left < 0:
                raise ValueError("source_catalog_json_budget_exceeded")
            raw = token.group()
            offset = token.end()
            if raw in ('{', '['):
                stack.append(raw)
                if len(stack) > MAX_JSON_DEPTH:
                    raise ValueError("source_catalog_json_budget_exceeded")
            elif raw in ('}', ']'):
                if not stack or stack.pop() != ('{' if raw == '}' else '['):
                    break
                if not stack:
                    try:
                        value = json.loads(body[start:offset])
                    except ValueError:
                        break
                    pending, values = [value], []
                    while pending:
                        child = pending.pop()
                        if isinstance(child, dict):
                            pending.extend(child.values())
                        else:
                            values.append(_json(child))
                    if values and all(value in typed_values for value in values):
                        objects.append(((start, offset), tuple(keys), tuple(leaves)))
                    break
            elif raw not in (',', ':'):
                following = _JSON_SPACE.match(body, offset).end()
                spans = keys if raw.startswith('"') and body[following:following + 1] == ':' else leaves
                spans.append((token.start(), token.end()))
        cursor = max(start + 1, offset)
    return tuple(objects)


def _structured_options(body, objects):
    """Four global choices: all keys, one key, one leaf, whole objects.

    All-key gaps keep compact value-only catalogs. Single-key/leaf alternatives
    recover from key/value collisions without an unbounded Cartesian search.
    Prefer gaps overlapping the fewest value words, independent of key order.
    """
    values = [body[a:b] for _, _, leaves in objects for a, b in leaves]
    def rank(span):
        text = body[span[0]:span[1]]
        words = re.findall(r"\w+", text)
        return (sum(word in value for word in words for value in values),
                len(words), text, span[0])
    keys = tuple(span for _, keys, _ in objects for span in keys)
    key_gaps, leaf_gaps = [], []
    for whole, object_keys, leaves in objects:
        key_gaps.append(min(object_keys, key=rank) if object_keys else whole)
        leaf_gaps.append(min(leaves, key=rank) if leaves else whole)
    return [keys or tuple(whole for whole, _, _ in objects),
            tuple(key_gaps), tuple(leaf_gaps), tuple(whole for whole, _, _ in objects)]


def _content_gap_spans(body):
    words = list(re.finditer(r"\w+", body, re.UNICODE))
    if not any(word.group().casefold() not in CONNECTIVE_GAPS for word in words):
        return ()
    return tuple((word.start(), word.end()) for word in words
                 if word.group().casefold() in CONNECTIVE_GAPS)


def _gap_options(body, document_frequency, pattern_count, typed_values, window_loss,
                 *, structured=None):
    """Rank gaps before selecting phrases; never remove record/field input.

    Parsed object leaves must already be present in the complete typed catalog.
    Identifier-shaped tokens are structural preferences, not semantic labels.
    Otherwise prefer corpus-repeated terms, then retain the most contiguous
    two/three-word windows. Equal single-pattern windows keep the prefix; in a
    multi-pattern corpus ties keep the suffix. Neither tie promises semantics.
    """
    structural = _structured_spans(body, typed_values) if structured is None else structured
    if structural:
        return _structured_options(body, structural)
    opaque = tuple((m.start(), m.end()) for m in re.finditer(
        r"(?<!\w)(?:[A-Z0-9]+(?:_[A-Z0-9]+)+|(?=[A-Za-z0-9_]*\d)[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+|[A-Za-z]+[-][A-Za-z0-9-]*\d[A-Za-z0-9-]*|[a-fA-F0-9]{16,})(?!\w)", body))
    if opaque:
        # A gap is a preference among payloads, not removal of every identifier.
        # Prefer exact values already represented by typed fields; then retain
        # the largest contiguous prefix. Alternative payload gaps stay bounded.
        ordered = sorted(opaque, key=lambda span: (
            _json(body[span[0]:span[1]]) not in typed_values, -span[0]))
        return [(span,) for span in ordered[:MAX_GAP_PLANS]]
    connectives = _content_gap_spans(body)
    if connectives:
        return [connectives]
    words = list(re.finditer(r"\w+", body, re.UNICODE))
    def rank(index):
        left, right = index, len(words) - index - 1
        windows = sum(max(0, length - width + 1)
                      for length in (left, right) for width in (2, 3))
        return (-document_frequency[words[index].group().casefold()],
                window_loss[words[index].group().casefold()], -windows,
                -index if pattern_count == 1 else index)
    frequency = max((document_frequency[word.group().casefold()] for word in words), default=0)
    eligible = [i for i, word in enumerate(words)
                if document_frequency[word.group().casefold()] == frequency]
    return [((words[i].start(), words[i].end()),)
            for i in sorted(eligible, key=rank)[:MAX_GAP_PLANS]] or [()]


def _crosses_reserved_gap(text, gaps):
    # All occurrences on ALL surfaces matter, including a match inside another
    # word in a nonwitness body. Exact phrase membership remains unchanged.
    for body, spans in gaps:
        offset = 0
        while (start := body.find(text, offset)) >= 0:
            if any(start < end and start + len(text) > begin for begin, end in spans):
                return True
            offset = start + 1
    return False


@dataclass(frozen=True)
class CatalogPhrase:
    phrase_ref: str
    text: str
    event_keys: tuple[str, ...]


@dataclass(frozen=True)
class SourceCatalog:
    """Call-local sidecar; never include this object in durable evidence."""

    views: tuple[tuple[str, str], ...]
    exclusions: tuple[tuple[str, tuple[str, ...]], ...]
    phrases: tuple[CatalogPhrase, ...]
    omissions: tuple[str, ...]

    def bind(self, records, source_views, protected_bodies):
        """Bind global physical IDs to complete ordered local memberships."""
        lookup = _lookup(records)
        if set(lookup) != set(source_views) or set(lookup) != set(protected_bodies):
            raise ValueError("source_catalog_scope_mismatch")
        views, exclusions = dict(self.views), dict(self.exclusions)
        keys = [row["event_key"] for row in records]
        if len(set(keys)) != len(keys):
            raise ValueError("source_catalog_binding_mismatch")
        for ref, key in zip(lookup, keys):
            bodies = protected_bodies[ref]
            if (not isinstance(bodies, (list, tuple, set))
                    or any(not isinstance(body, str) for body in bodies)
                    or key not in views or source_views[ref] != views[key]
                    or tuple(sorted(set(bodies))) != exclusions[key]):
                raise ValueError("source_catalog_binding_mismatch")
        bound = []
        for phrase in self.phrases:
            refs = [ref for ref, key in zip(lookup, keys) if key in phrase.event_keys]
            # Membership is recomputed even when a tampered/stale entry claims
            # no local members; false absence must not evade the binding check.
            if refs != [ref for ref in lookup if phrase.text in source_views[ref]]:
                raise ValueError("source_catalog_membership_mismatch")
            if refs:
                bound.append({"phrase_ref": phrase.phrase_ref, "text": phrase.text,
                              "record_ref": refs[0], "event_refs": refs})
        return bound


def _proposals(text):
    """Whole-word contiguous windows at EVERY position, never a prefix sample."""
    words = list(re.finditer(r"\w+", text, re.UNICODE))
    for start in range(len(words)):
        for width in range(1, min(MAX_PROPOSAL_WORDS, len(words) - start) + 1):
            end = words[start + width - 1].end()
            if text[end:end + 2] == "()":
                end += 2
            fragment = text[words[start].start():end]
            if len(fragment) > MAX_PHRASE_CHARS:
                break
            if len(fragment) >= 2 and any(char.isalnum() for char in fragment):
                yield fragment, width


def build_source_catalog(source_views, protected_bodies, *, typed_values=()):
    """Propose, deduplicate and globally prune BEFORE packing or generation.

    Every distinct view receives a turn before a second proposal from any view.
    Choose structural/typed payload gaps first, then connective gaps, otherwise
    corpus-repeated lexical gaps. Four bounded global assignments are ranked by
    covered patterns, exact structured value windows/leaves, structured catalog
    cost, retained prose windows, distinct window rarity, and stable plan order.
    All occurrences on all body surfaces are guarded. Hashes order proposals only AFTER gaps are explicitly planned.
    Coverage is not a semantic promise: exhausted plans report a conflict, while
    views with no individually safe proposal have explicit checked omissions.
    All original records and fields remain complete input.
    """
    if (not isinstance(source_views, dict) or not source_views
            or set(source_views) != set(protected_bodies)
            or any(not isinstance(key, str) or not key or not isinstance(text, str) or not text
                   for key, text in source_views.items())):
        raise ValueError("source_catalog_scope_mismatch")
    exclusions = {}
    for key, values in protected_bodies.items():
        if (not isinstance(values, (list, tuple, set))
                or any(not isinstance(body, str) for body in values)):
            raise ValueError("invalid_observation_body_exclusions")
        exclusions[key] = tuple(sorted(set(values)))
    bodies = tuple(sorted(set(source_views.values()).union(
        body for values in exclusions.values() for body in values)))
    frequencies = Counter(source_views.values())
    patterns = sorted(frequencies, key=lambda text: (frequencies[text], sha256(text.encode()).digest()))
    documents = Counter(word for pattern in patterns
                        for word in set(re.findall(r"\w+", pattern.casefold())))
    window_loss = Counter()
    for pattern in patterns:
        words = re.findall(r"\w+", pattern.casefold())
        for width in (2, 3):
            for start in range(len(words) - width + 1):
                window_loss.update(set(words[start:start + width]))
    typed_values = set(typed_values)
    structured = {body: _structured_spans(body, typed_values) for body in bodies}
    options = [(body, _gap_options(body, documents, len(patterns), typed_values, window_loss,
                                 structured=structured[body])) for body in bodies]
    value_fragments = {pattern: tuple(pattern[a:b] for _, _, leaves in structured[pattern]
                                      for a, b in leaves) for pattern in patterns}
    proposals, checked, individually_safe, count = {}, {}, {}, 0
    for pattern in patterns:
        candidates = {}
        # Structured proposals stay within literal leaves or surrounding prose;
        # crossing keys/separators adds capacity cost without value content.
        fragments, cursor = [], 0
        for (start, end), _, leaves in structured[pattern]:
            fragments.append(pattern[cursor:start])
            fragments.extend(pattern[a:b] for a, b in leaves)
            cursor = end
        fragments.append(pattern[cursor:])
        for text, width in _proposals(pattern):
            count += 1
            if count > MAX_PROPOSALS:
                raise ValueError("source_catalog_proposal_budget_exceeded")
            if text not in checked:
                try:
                    _protect_bodies([text], bodies)
                except ValueError:
                    checked[text] = False
                else:
                    checked[text] = True
            if checked[text]:
                individually_safe[pattern] = True
            if checked[text] and any(text in fragment for fragment in fragments):
                candidates[text] = width
        proposals[pattern] = sorted(candidates, key=lambda text: (
            {2: 0, 3: 1, 1: 2}.get(candidates[text], candidates[text]),
            sha256(text.encode()).digest()))
    # Four explicit global assignments, with complete all-body union checks.
    # Each unique proposal is generated once and evaluated at most four times.
    # Exhaustion is a conflict, not proof that every assignment is infeasible.
    plans = []
    for plan in range(MAX_GAP_PLANS):
        gaps = [(body, choices[min(plan, len(choices) - 1)]) for body, choices in options]
        accepted, accepted_set = [], set()
        for index in range(max((len(values) for values in proposals.values()), default=0)):
            for pattern in patterns:
                if index >= len(proposals[pattern]):
                    continue
                text = proposals[pattern][index]
                if text in accepted_set or _crosses_reserved_gap(text, gaps):
                    continue
                try:
                    _protect_bodies([*accepted, text], bodies)
                except ValueError:
                    continue
                if len(accepted) == MAX_CATALOG_PHRASES:
                    raise ValueError("source_catalog_size_exceeded")
                accepted.append(text)
                accepted_set.add(text)
        coverage = sum(any(text in pattern for text in accepted) for pattern in patterns)
        # Distinguishing contiguous windows contribute more than shared windows.
        score = sum(1 / sum(text in pattern for pattern in patterns)
                    for text in accepted if 2 <= len(re.findall(r"\w+", text)) <= 3)
        windows = sum(sum(text in pattern for pattern in patterns)
                      for text in accepted if 2 <= len(re.findall(r"\w+", text)) <= 3)
        # Count exact leaf content, not merely a boilerplate prefix or JSON
        # keys. Whole-object capacity plans cannot win by prefix coverage.
        value_windows = sum(sum(text in leaf for text in accepted
                                if 2 <= len(re.findall(r"\w+", text)) <= 3)
                            for leaves in value_fragments.values() for leaf in leaves)
        value_coverage = sum(any(text in leaf for text in accepted)
                             for leaves in value_fragments.values() for leaf in leaves)
        structured_cost = sum(len(text) for text in accepted
                              if any(text in pattern[a:b] for pattern in patterns
                                     for (a, b), _, _ in structured[pattern]))
        plans.append(((coverage, value_windows, value_coverage, -structured_cost,
                       windows, score, -plan), accepted))
    accepted = max(plans, key=lambda item: item[0])[1]
    # Coverage includes matching proposals originating at ANY other pattern.
    # Never quietly sacrifice a rare pattern to the global privacy invariant.
    omissions = []
    for key, pattern in source_views.items():
        if not any(text in pattern for text in accepted):
            if individually_safe.get(pattern):
                raise ValueError("source_catalog_coverage_conflict")
            omissions.append(key)
    _protect_bodies(accepted, bodies)
    return SourceCatalog(tuple(source_views.items()), tuple(exclusions.items()), tuple(
        CatalogPhrase(f"p{index}", text, tuple(key for key, view in source_views.items() if text in view))
        for index, text in enumerate(accepted)), tuple(omissions))


def select_catalog_observations(value, records, source_views, protected_bodies,
                                field_refs, catalog, *, output_budget):
    """Strict new transport; validate ALL incoming refs before deduplication.

    At most four phrase and 64 field refs may arrive, even if redundant. Exact
    repeated known refs are normalized in first-occurrence order. Unknown IDs,
    invalid types and extra keys cannot hide behind redundancy. The caller owns
    the unchanged mandatory field union check.
    """
    if (not isinstance(value, dict) or set(value) != {"phrase_refs", "important_fields"}
            or len(_json(value)) > output_budget):
        raise ValueError("invalid_source_catalog_output")
    bound = catalog.bind(records, source_views, protected_bodies)
    lookup = {item["phrase_ref"]: item for item in bound}
    refs = value["phrase_refs"]
    if (not isinstance(refs, list) or len(refs) > MAX_PHRASES
            or any(not isinstance(ref, str) or ref not in lookup for ref in refs)):
        raise ValueError("invalid_source_catalog_refs")
    fields = value["important_fields"]
    if (not isinstance(fields, list) or len(fields) > 64
            or any(not isinstance(ref, str) or ref not in field_refs for ref in fields)):
        raise ValueError("invalid_source_observation_fields")
    phrases = [{key: lookup[ref][key] for key in ("record_ref", "text")}
               for ref in dict.fromkeys(refs)]
    expanded = {"source_phrases": phrases, "important_fields": list(dict.fromkeys(fields))}
    # The transport budget applies to the actual ref response. Expansion is code
    # derived, separately reserved by the caller; it is never model prose.
    observations, fields = select_observations(
        expanded, records, source_views, protected_bodies, field_refs,
        output_budget=len(_json(expanded)))
    _protect_bodies([item["text"] for item in observations],
                    [*dict(catalog.views).values(),
                     *(body for _, values in catalog.exclusions for body in values)])
    return observations, fields
