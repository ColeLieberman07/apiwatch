"""Compare two snapshots and report what actually changed.

Severity is the product. A tool that reports everything is a tool nobody
reads. Three levels:

  BREAKING  - a field disappeared, a type changed, a status code changed,
              or a pinned error code changed. Your code may already be broken.
  ADDITIVE  - a new field appeared. Not urgent, but it's the vendor shipping
              something they may not have announced.
  INFO      - latency shifts, header changes, optionality flips.
"""

BREAKING, ADDITIVE, INFO = "BREAKING", "ADDITIVE", "INFO"

# Below this, latency movement is noise.
LATENCY_FACTOR = 2.5
LATENCY_FLOOR_MS = 300


def _by_name(snap: dict) -> dict:
    return {p["probe"]: p for p in snap.get("probes", [])}


def compare(old: dict, new: dict) -> list[dict]:
    findings: list[dict] = []
    old_probes, new_probes = _by_name(old), _by_name(new)

    for name in sorted(set(old_probes) | set(new_probes)):
        before, after = old_probes.get(name), new_probes.get(name)

        if before is None:
            findings.append({"probe": name, "level": INFO, "what": "new probe added"})
            continue
        if after is None:
            findings.append({"probe": name, "level": INFO, "what": "probe removed"})
            continue

        # A probe that failed to connect tells us nothing. Skip rather than
        # cry wolf — network flakes would otherwise dominate the report.
        if "error" in before or "error" in after:
            if "error" in after and "error" not in before:
                findings.append(
                    {"probe": name, "level": INFO,
                     "what": f"probe failed to run: {after['error']}"}
                )
            continue

        # Same logic for rate limits and upstream 5xx: report it once as
        # INFO so the operator knows coverage has a hole, then move on.
        # Never let it reach the schema diff.
        if before.get("unreliable") or after.get("unreliable"):
            reason = after.get("unreliable") or before.get("unreliable")
            findings.append(
                {"probe": name, "level": INFO,
                 "what": f"skipped, not a drift signal: {reason}"}
            )
            continue

        findings.extend(_compare_probe(name, before, after))

    return findings


def _compare_probe(name: str, before: dict, after: dict) -> list[dict]:
    out: list[dict] = []

    if before["status"] != after["status"]:
        out.append({
            "probe": name, "level": BREAKING,
            "what": f"status code {before['status']} -> {after['status']}",
        })

    old_schema, new_schema = before["schema"], after["schema"]

    for path in sorted(set(old_schema) - set(new_schema)):
        out.append({
            "probe": name, "level": BREAKING,
            "what": f"field removed: {path} (was {old_schema[path]})",
        })

    for path in sorted(set(new_schema) - set(old_schema)):
        out.append({
            "probe": name, "level": ADDITIVE,
            "what": f"field added: {path} ({new_schema[path]})",
        })

    for path in sorted(set(old_schema) & set(new_schema)):
        old_type, new_type = old_schema[path], new_schema[path]
        if old_type == new_type:
            continue
        # A bare '?' flip is just an absent optional value in this sample.
        if old_type.rstrip("?") == new_type.rstrip("?"):
            out.append({
                "probe": name, "level": INFO,
                "what": f"optionality changed: {path} {old_type} -> {new_type}",
            })
        else:
            out.append({
                "probe": name, "level": BREAKING,
                "what": f"type changed: {path} {old_type} -> {new_type}",
            })

    for key in sorted(set(before["pinned"]) | set(after["pinned"])):
        old_value = before["pinned"].get(key)
        new_value = after["pinned"].get(key)
        if old_value != new_value:
            out.append({
                "probe": name, "level": BREAKING,
                "what": f"pinned value changed: {key} {old_value!r} -> {new_value!r}",
            })

    for key in sorted(set(before["headers"]) | set(after["headers"])):
        old_value = before["headers"].get(key)
        new_value = after["headers"].get(key)
        if old_value != new_value:
            level = BREAKING if key in ("deprecation", "sunset") else INFO
            out.append({
                "probe": name, "level": level,
                "what": f"header {key}: {old_value!r} -> {new_value!r}",
            })

    old_ms, new_ms = before.get("latency_ms", 0), after.get("latency_ms", 0)
    if old_ms and new_ms > LATENCY_FLOOR_MS and new_ms > old_ms * LATENCY_FACTOR:
        out.append({
            "probe": name, "level": INFO,
            "what": f"latency {old_ms}ms -> {new_ms}ms",
        })

    return out


def render(target: str, findings: list[dict]) -> str:
    if not findings:
        return f"[{target}] no changes"

    order = {BREAKING: 0, ADDITIVE: 1, INFO: 2}
    findings = sorted(findings, key=lambda f: (order[f["level"]], f["probe"]))

    counts = {level: sum(1 for f in findings if f["level"] == level)
              for level in (BREAKING, ADDITIVE, INFO)}
    header = (f"[{target}] {counts[BREAKING]} breaking, "
              f"{counts[ADDITIVE]} additive, {counts[INFO]} info")

    lines = [header, "=" * len(header)]
    for finding in findings:
        lines.append(f"  {finding['level']:<9} {finding['probe']}: {finding['what']}")
    return "\n".join(lines)


def has_breaking(findings: list[dict]) -> bool:
    return any(f["level"] == BREAKING for f in findings)
