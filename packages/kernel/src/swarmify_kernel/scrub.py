"""Secret values kept out of everything stored or shown (spec: World Logins, Scrubbing).

While a stage holds a World Login, its values (password, authenticator secret, the session's
cookie values, a code typed at a Person Check) are listed here, in memory only, by stage and run.
Whatever the core stores or shows from that stage (call records, events, transcript lines, stderr
tails, errors, text artifacts) passes through `text` or `value` first: each exact value becomes
`[secret: <login>]`. Plain substring replacement, no guessing at encodings. Shared here because
Connectors lists the values while the event log, the swarm's stage log and Runs' errors consult
them.
"""

import base64
import json
import threading
from typing import Any
from urllib.parse import quote, quote_plus

_lock = threading.Lock()
_held: dict[str, tuple[str, str | None, dict[str, str]]] = {}
"""Stage id to its run id, its world id and its values, each to the login it belongs to."""
MIN = 6
"""Shorter values are never listed: replacing them would mangle ordinary text. A login refuses
a shorter password; a short Person Check code goes unscrubbed."""


def forms(value: str, username: str | None = None) -> list[str]:
    """`value` as it may appear in stored text: itself, inside a JSON string (ASCII-escaped or
    not), URL-encoded (as a path or a form field) and, with `username`, in an HTTP Basic header
    (base64 of `username:value`)."""
    out = [
        value,
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        quote(value, safe=""),
        quote_plus(value),
    ]
    if username:
        out.append(base64.b64encode(f"{username}:{value}".encode()).decode())
    return list(dict.fromkeys(out))


def add(
    stage_id: str,
    run_id: str,
    login: str,
    values: list[str | None],
    username: str | None = None,
    world_id: str | None = None,
) -> None:
    """List `values` of `login`, in each of their `forms`, as the stage's secrets; empty ones
    and those under MIN characters are left out."""
    with _lock:
        _, _, held = _held.setdefault(stage_id, (run_id, world_id, {}))
        for v in values:
            if v and len(v) >= MIN:
                held.update(dict.fromkeys(forms(v, username), login))


def holds(stage_id: str) -> bool:
    with _lock:
        return stage_id in _held


def cookies(session: str | None) -> list[str | None]:
    """The values in a Saved Session worth scrubbing: every `value` (a cookie's, a stored
    item's) at least 8 characters long, as Playwright's storage state keeps them."""
    found: list[str | None] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            v = x.get("value")
            if isinstance(v, str) and len(v) >= 8:
                found.append(v)
            for item in x.values():
                walk(item)
        elif isinstance(x, list):
            for item in x:
                walk(item)

    try:
        walk(json.loads(session or "null"))
    except ValueError:
        pass
    return found


def drop(stage_id: str) -> None:
    """The stage settled: forget its values."""
    with _lock:
        _held.pop(stage_id, None)


def _pairs(
    stage_id: str | None, run_id: str | None, world_id: str | None = None
) -> list[tuple[str, str]]:
    """The values that apply, longest first: the stage's, its run's stages', its world's
    stages' (for what belongs to no run), or all when nothing says whose."""
    anyone = stage_id is None and run_id is None and world_id is None
    with _lock:
        found: dict[str, str] = {}
        for stage, (run, world, held) in _held.items():
            if anyone or stage == stage_id or run == run_id or (world and world == world_id):
                found.update(held)
    return sorted(found.items(), key=lambda p: -len(p[0]))


def text(
    said: str, stage_id: str | None = None, run_id: str | None = None, world_id: str | None = None
) -> str:
    """`said` with every listed value replaced by its placeholder."""
    for secret, login in _pairs(stage_id, run_id, world_id):
        said = said.replace(secret, f"[secret: {login}]")
    return said


def value(
    data: Any, stage_id: str | None = None, run_id: str | None = None, world_id: str | None = None
) -> Any:
    """`data` (JSON-like) with every string in it scrubbed; keys kept."""
    pairs = _pairs(stage_id, run_id, world_id)
    if not pairs:
        return data

    def go(x: Any) -> Any:
        if isinstance(x, str):
            for secret, login in pairs:
                x = x.replace(secret, f"[secret: {login}]")
            return x
        if isinstance(x, dict):
            return {k: go(v) for k, v in x.items()}
        if isinstance(x, list | tuple):
            return [go(v) for v in x]
        return x

    return go(data)
