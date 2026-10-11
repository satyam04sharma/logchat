"""Lossless request-local message/table factoring; never a token estimate.

Only the model input is encoded. Canonical records, selector choices and output
schemas remain outside this codec. Callers must validate against their complete
unfactored request before packing or preflight, then count the encoded request.
"""
from copy import deepcopy
import json
import math


COMPACT_INPUT_VERSION = "compact-message-dictionary-v1"
COLUMNAR_INPUT_VERSION = "compact-columnar-v2"
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


def _pack_table(rows):
    # Sorted explicit headers are independent of JSON object key order. A mask
    # is necessary only for heterogeneous keys: missing must never become null.
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        _fail()
    columns = sorted({key for row in rows for key in row})
    table = {"columns": columns,
             "rows": [[row.get(key) for key in columns] for row in rows]}
    if any(len(row) != len(columns) for row in rows):
        table["presence"] = [[key in row for key in columns] for row in rows]
    return table


def _unpack_table(table):
    if (type(table) is not dict or set(table) not in
            ({"columns", "rows"}, {"columns", "rows", "presence"})):
        _fail()
    columns, rows = table["columns"], table["rows"]
    if (type(columns) is not list or any(type(key) is not str for key in columns)
            or len(set(columns)) != len(columns) or type(rows) is not list):
        _fail()
    presence = table.get("presence")
    if "presence" in table and (type(presence) is not list or len(presence) != len(rows)):
        _fail()
    result = []
    for i, row in enumerate(rows):
        if type(row) is not list or len(row) != len(columns):
            _fail()
        mask = presence[i] if presence is not None else [True] * len(columns)
        if (type(mask) is not list or len(mask) != len(columns)
                or any(type(flag) is not bool for flag in mask)):
            _fail()
        if any(not flag and value is not None for flag, value in zip(mask, row)):
            _fail()
        result.append({key: value for key, value, flag in zip(columns, row, mask) if flag})
    return result


def project_compact_input(original, *, version=COMPACT_INPUT_VERSION):
    """Factor exact strings and optionally table keys, retaining all typed data."""
    _typed_json(original)
    if version not in (COMPACT_INPUT_VERSION, COLUMNAR_INPUT_VERSION):
        _fail()
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
    projected["input_projection"] = version
    projected["message_dictionary"] = dictionary
    if version == COLUMNAR_INPUT_VERSION:
        for name in ("records", "field_catalog"):
            if name in projected:
                projected[name] = _pack_table(projected[name])
    return projected


def expand_compact_input(projected):
    """Decode strict historical v1 or columnar v2 without changing saved data."""
    spelling = _typed_json(projected)
    if type(projected) is not dict:
        _fail()
    version = projected.get("input_projection")
    if version not in (COMPACT_INPUT_VERSION, COLUMNAR_INPUT_VERSION):
        _fail()
    unpacked = deepcopy(projected)
    if version == COLUMNAR_INPUT_VERSION:
        for name in ("records", "field_catalog"):
            if name in unpacked:
                unpacked[name] = _unpack_table(unpacked[name])
    rows = _records(unpacked, encoded=True)
    dictionary = projected.get("message_dictionary")
    if type(dictionary) is not dict or any(type(v) is not str for v in dictionary.values()):
        _fail()
    expanded = unpacked
    del expanded["input_projection"]
    del expanded["message_dictionary"]
    for row, target in zip(rows, expanded["records"]):
        ref = row["message_ref"]
        if ref not in dictionary:
            _fail()
        del target["message_ref"]
        target["message"] = dictionary[ref]
    # Reject aliases, unused/duplicate entries, and noncanonical first-use refs.
    if _typed_json(project_compact_input(expanded, version=version)) != spelling:
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
