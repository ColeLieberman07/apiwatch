"""Infer a structural schema from a JSON value.

The whole point: we record *shape*, not *values*. Values change every call
(ids, timestamps, counts) and would drown you in false positives. Types,
field names, and optionality only change when the vendor changes something.
"""

from typing import Any


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


# An object whose keys are *data* (version numbers, ids, dates) is a map,
# not a struct. Left alone, one npm package expands to 25,000 schema paths
# and every new release looks like thousands of added fields.
MAP_KEY_THRESHOLD = 12


def _looks_like_map(value: dict) -> bool:
    if len(value) <= MAP_KEY_THRESHOLD:
        return False
    kinds = {
        "object" if isinstance(v, dict)
        else "array" if isinstance(v, list)
        else "scalar"
        for v in value.values()
    }
    return len(kinds) == 1


def infer(value: Any) -> dict:
    """Build a schema tree for a decoded JSON value."""
    if isinstance(value, dict):
        if _looks_like_map(value):
            items = list(value.values())
            merged = infer(items[0])
            for item in items[1:]:
                merged = merge(merged, infer(item))
            return {"kind": "map", "values": merged, "key_count": len(value)}
        return {
            "kind": "object",
            "fields": {k: infer(v) for k, v in sorted(value.items())},
        }
    if isinstance(value, list):
        if not value:
            return {"kind": "array", "items": {"kind": "unknown"}}
        merged = infer(value[0])
        for item in value[1:]:
            merged = merge(merged, infer(item))
        return {"kind": "array", "items": merged, "sample_size": len(value)}
    return {"kind": "scalar", "type": _type_name(value)}


def merge(a: dict, b: dict) -> dict:
    """Merge two schemas describing elements of the same collection.

    Fields present in only one side get marked optional — that distinction
    is often the earliest signal that a vendor started conditionally
    returning something new.
    """
    if a.get("kind") != b.get("kind"):
        return {"kind": "union", "of": sorted({_flatten(a), _flatten(b)})}

    if a["kind"] == "object":
        fields: dict[str, Any] = {}
        for key in sorted(set(a["fields"]) | set(b["fields"])):
            in_a, in_b = key in a["fields"], key in b["fields"]
            if in_a and in_b:
                fields[key] = merge(a["fields"][key], b["fields"][key])
            else:
                only = a["fields"][key] if in_a else b["fields"][key]
                fields[key] = {**only, "optional": True}
        return {"kind": "object", "fields": fields}

    if a["kind"] == "array":
        return {"kind": "array", "items": merge(a["items"], b["items"])}

    if a["kind"] == "map":
        return {"kind": "map", "values": merge(a["values"], b["values"])}

    if a["kind"] == "scalar":
        if a["type"] == b["type"]:
            return a
        # int/float drift is a real signal, but not worth alerting on
        if {a["type"], b["type"]} == {"int", "float"}:
            return {"kind": "scalar", "type": "number"}
        return {"kind": "union", "of": sorted({a["type"], b["type"]})}

    return a


def _flatten(node: dict) -> str:
    kind = node.get("kind")
    if kind == "scalar":
        return node.get("type", "scalar")
    return kind or "unknown"


def paths(node: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a schema into {json.path: type} for easy diffing."""
    out: dict[str, str] = {}
    kind = node.get("kind")

    if kind == "object":
        for key, child in node.get("fields", {}).items():
            here = f"{prefix}.{key}" if prefix else key
            label = _flatten(child)
            if child.get("optional"):
                label += "?"
            out[here] = label
            out.update(paths(child, here))
    elif kind == "array":
        here = f"{prefix}[]"
        items = node.get("items", {})
        out[here] = _flatten(items)
        out.update(paths(items, here))
    elif kind == "map":
        here = f"{prefix}{{*}}"
        values = node.get("values", {})
        out[here] = _flatten(values)
        out.update(paths(values, here))

    return out
