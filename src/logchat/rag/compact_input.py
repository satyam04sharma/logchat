"""Lossless request-local message factoring; never a token or latency estimate.

Only the model input is encoded. Canonical records, selector choices and output
schemas remain outside this codec. Callers must validate against their complete
unfactored request before packing or preflight, then count the encoded request.
"""
from copy import deepcopy
import json
import math


COMPACT_INPUT_VERSION = "compact-message-dictionary-v1"
_RESERVED = {"input_projection", "message_dictionary"}


def _fail():
    # Never include source bodies or field values in diagnostics.
    raise ValueError("compact_input_projection_invalid")


def _typed_json(value):
    """Reject coercions; JSON spelling distinguishes bool/int/float and -0.0."""
    def check(item):
        kind = type(item)
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    _fail()
                check(child)
        elif kind is list:
            for child in item:
                check(child)
        elif kind is float:
            if not math.isfinite(item):
                _fail()
        elif kind not in (str, int, bool, type(None)):
            _fail()
    check(value)
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _records(payload, *, encoded):
    if type(payload) is not dict or type(payload.get("records")) is not list:
        _fail()
    refs = set()
    for row in payload["records"]:
        if (type(row) is not dict or type(row.get("ref")) is not str
                or not row["ref"] or row["ref"] in refs):
            _fail()
        refs.add(row["ref"])
        field, forbidden = ("message_ref", "message") if encoded else ("message", "message_ref")
        if type(row.get(field)) is not str or forbidden in row:
            _fail()
    return payload["records"]


def project_compact_input(original):
    """Factor exact strings in first-occurrence order, retaining all other data."""
    _typed_json(original)
    rows = _records(original, encoded=False)
    if _RESERVED.intersection(original):
        _fail()
    projected = deepcopy(original)
    dictionary, by_message = {}, {}
    for row, target in zip(rows, projected["records"]):
        message = row["message"]
        if message not in by_message:
            ref = "m" + str(len(dictionary))
            by_message[message] = ref
            dictionary[ref] = message
        del target["message"]
        target["message_ref"] = by_message[message]
    projected["input_projection"] = COMPACT_INPUT_VERSION
    projected["message_dictionary"] = dictionary
    return projected


def expand_compact_input(projected):
    """Decode only the deterministic v1 form, including saved request payloads."""
    spelling = _typed_json(projected)
    rows = _records(projected, encoded=True)
    if projected.get("input_projection") != COMPACT_INPUT_VERSION:
        _fail()
    dictionary = projected.get("message_dictionary")
    if type(dictionary) is not dict or any(type(v) is not str for v in dictionary.values()):
        _fail()
    expanded = deepcopy(projected)
    del expanded["input_projection"]
    del expanded["message_dictionary"]
    for row, target in zip(rows, expanded["records"]):
        ref = row["message_ref"]
        if ref not in dictionary:
            _fail()
        del target["message_ref"]
        target["message"] = dictionary[ref]
    # Reject aliases, unused/duplicate entries, and noncanonical first-use refs.
    if _typed_json(project_compact_input(expanded)) != spelling:
        _fail()
    return expanded


def validate_compact_input_projection(original, projected):
    """Assert complete typed equality, including ordered membership and choices.

    Structural validity alone cannot detect a valid reference swapped to another
    message. This comparison against the former complete input is mandatory.
    """
    expected = _typed_json(original)
    _records(original, encoded=False)
    if _RESERVED.intersection(original):
        _fail()
    if _typed_json(expand_compact_input(projected)) != expected:
        raise ValueError("compact_input_projection_mismatch")
