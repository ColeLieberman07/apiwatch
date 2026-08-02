"""Run probes against a target API and record what came back.

We record four things per probe:
  1. HTTP status code
  2. Response body *schema* (not values)
  3. A whitelist of headers that carry vendor signals
  4. Explicitly pinned values (error codes, enum-ish strings)

The error probes matter as much as the happy path. Vendors change error
codes, messages, and status codes constantly and almost never write it down.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from . import schema

# Headers worth watching. Sunset/Deprecation are RFC 8594 — vendors that
# use them are telling you about a breaking change months ahead, and almost
# nobody reads them.
WATCHED_HEADERS = [
    "deprecation",
    "sunset",
    "link",
    "x-api-version",
    "stripe-version",
    "x-github-api-version-selected",
    "x-ratelimit-limit",
    "x-ratelimit-resource",
    "content-type",
]

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(text: str) -> str:
    """Substitute ${ENV_VAR} so secrets never live in the config file."""
    return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), text)


def _pin(data: Any, path: str) -> Any:
    """Pluck a value at a dotted path, e.g. 'error.type' or 'items[].status'."""
    current = data
    for part in path.split("."):
        if part.endswith("[]"):
            key = part[:-2]
            if key:
                current = current.get(key) if isinstance(current, dict) else None
            if not isinstance(current, list) or not current:
                return None
            current = current[0]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def run(target: dict, probe: dict, timeout: int = 20) -> dict:
    """Execute a single probe and return its recorded state."""
    url = _expand(target["base_url"].rstrip("/") + probe["path"])
    method = probe.get("method", "GET").upper()

    headers = {"User-Agent": "apiwatch/0.1 (schema drift monitor)"}
    for key, value in {**target.get("headers", {}), **probe.get("headers", {})}.items():
        expanded = _expand(str(value))
        if expanded:
            headers[key] = expanded

    body = None
    if probe.get("body") is not None:
        body = json.dumps(probe["body"]).encode()
        headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=body, headers=headers, method=method)

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
            response_headers = dict(response.headers)
    except urllib.error.HTTPError as err:
        # Expected for error probes — this is the interesting path.
        status = err.code
        raw = err.read()
        response_headers = dict(err.headers)
    except Exception as err:  # noqa: BLE001 - network flake, not a drift signal
        return {"probe": probe["name"], "error": f"{type(err).__name__}: {err}"}

    elapsed_ms = int((time.monotonic() - started) * 1000)

    try:
        decoded = json.loads(raw)
        body_schema = schema.paths(schema.infer(decoded))
    except (json.JSONDecodeError, UnicodeDecodeError):
        decoded = None
        body_schema = {"<non-json>": f"{len(raw)} bytes"}

    pinned = {}
    for path in probe.get("pin_values", []):
        value = _pin(decoded, path) if decoded is not None else None
        pinned[path] = value

    lowered = {k.lower(): v for k, v in response_headers.items()}
    watched = {h: lowered[h] for h in WATCHED_HEADERS if h in lowered}
    # Rate limit *ceilings* are signal; remaining counts are noise.
    watched.pop("x-ratelimit-remaining", None)

    # The single most important guard in this tool. A 429 or a 503 is not
    # the vendor changing their schema, it is you failing to reach them.
    # Diffing those responses would report "every field removed" and train
    # the user to ignore your alerts within a week.
    expected = probe.get("expect_status", 200)
    unreliable = None
    if status in (401, 403, 407, 429) and status != expected:
        unreliable = f"auth/rate-limit response ({status})"
    elif status >= 500 and status != expected:
        unreliable = f"upstream error ({status})"

    return {
        "probe": probe["name"],
        "status": status,
        "expect_status": expected,
        "unreliable": unreliable,
        "schema": body_schema,
        "headers": watched,
        "pinned": pinned,
        "latency_ms": elapsed_ms,
    }


def snapshot(target: dict) -> dict:
    """Run every probe for a target."""
    return {
        "target": target["name"],
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probes": [run(target, p) for p in target["probes"]],
    }
