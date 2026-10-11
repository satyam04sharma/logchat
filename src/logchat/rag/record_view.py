"""Reversible, model-only record projection; never a storage or identity codec."""
from __future__ import annotations

import json
from typing import Any


CODEC = "relevance-record-columns-v1"


def _wire(value: Any) -> str:
    # JSON spelling distinguishes bool/int/float and signed floating zero,
    # including nested values. Do not use Python equality to hoist fields.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def project_records(records: list[dict]) -> list[dict] | dict:
    """Hoist identical typed values only when the complete view is shorter.

    Heterogeneous key sets stay ordinary: missing is never encoded as null.
    The list/object distinction frames the codec, so source field names cannot
    collide with representation fields. Record order and key order are retained.
    """
    if len(records) < 2 or any(set(row) != set(records[0]) for row in records[1:]):
        return records
    keys = list(records[0])
    shared = {key: records[0][key] for key in keys
              if all(_wire(row[key]) == _wire(records[0][key]) for row in records[1:])}
    columns = [key for key in keys if key not in shared]
    view = {"codec": CODEC, "keys": keys, "shared": shared, "columns": columns,
            "rows": [[row[key] for key in columns] for row in records]}
    orders = [list(row) for row in records]
    if any(order != keys for order in orders):
        view["key_orders"] = orders
    return view if len(_wire(view).encode("utf-8")) < len(_wire(records).encode("utf-8")) else records


def restore_records(view: list[dict] | dict) -> list[dict]:
    """Pure inverse of project_records, preserving exact typed JSON bytes."""
    if isinstance(view, list):
        return view
    if view["codec"] != CODEC:
        raise ValueError("unsupported_relevance_record_codec")
    restored = []
    for index, row in enumerate(view["rows"]):
        values = {**view["shared"], **dict(zip(view["columns"], row, strict=True))}
        keys = view.get("key_orders", [view["keys"]] * len(view["rows"]))[index]
        restored.append({key: values[key] for key in keys})
    return restored
