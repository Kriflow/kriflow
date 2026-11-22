"""Secret values kept out of everything stored or shown (spec: World Logins, Scrubbing).

While a stage holds a World Login, its values (password, authenticator secret, the session's
cookie values, a code typed at a Person Check) are listed here, in memory only, by stage and run.
Whatever the core stores or shows from that stage (call records, events, transcript lines, stderr
tails, errors, text artifacts) passes through `text` or `value` first: each exact value becomes
`[secret: <login>]`. Plain substring replacement, no guessing at encodings. Shared here because
Connectors lists the values while the event log, the swarm's stage log and Runs' errors consult
them.
"""

import json
import threading
from typing import Any

_lock = threading.Lock()
_held: dict[str, tuple[str, dict[str, str]]] = {}
"""Stage id to its run id and its values, each to the login it belongs to."""


def add(stage_id: str, run_id: str, login: str, values: list[str | None]) -> None:
    """List `values` of `login` as the stage's secrets; empty ones are left out."""
    with _lock:
        _, held = _held.setdefault(stage_id, (run_id, {}))
        held.update({v: login for v in values if v})


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


def _pairs(stage_id: str | None, run_id: str | None) -> list[tuple[str, str]]:
    """The values that apply, longest first: the stage's, the run's stages', or all."""
    with _lock:
        found: dict[str, str] = {}
        for stage, (run, held) in _held.items():
            if (stage_id is None and run_id is None) or stage == stage_id or run == run_id:
                found.update(held)
    return sorted(found.items(), key=lambda p: -len(p[0]))


def text(said: str, stage_id: str | None = None, run_id: str | None = None) -> str:
    """`said` with every listed value replaced by its placeholder."""
    for secret, login in _pairs(stage_id, run_id):
        said = said.replace(secret, f"[secret: {login}]")
    return said


def value(data: Any, stage_id: str | None = None, run_id: str | None = None) -> Any:
    """`data` (JSON-like) with every string in it scrubbed; keys kept."""
    pairs = _pairs(stage_id, run_id)
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
