"""Resources (ADR 0030): a Resource is a folder of Slot files, `<i>.lock`, shared install-wide.
A process holds Slot i by an exclusive `flock` on its file, so any exit, a crash or a kill too,
frees it, and writes `<i>.holder` saying who holds it. A process with no free Slot writes a note
in `waiting/`, which it also holds locked, and tries again every half second. A note whose file
nobody holds locked is stale and ignored.

Packages carry a copy of `hold` (they cannot import the core); the core uses it for a Manifest's
`hold`, around a whole stage call."""

import fcntl
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

from kriflow_kernel.settings import setting

NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
COUNTS = "RESOURCES"
"""The setting (KRIFLOW_ or SWARMIFY_) of the operator's Slot counts, as `gpu=1,seat=2`; a
Resource it does not name has one."""


def counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for pair in setting(COUNTS).split(","):
        name, _, n = pair.strip().partition("=")
        if NAME.match(name) and n.strip().isdigit() and int(n) > 0:
            out[name] = int(n)
    return out


def env_name(name: str) -> str:
    return f"SWARMIFY_RESOURCE_{name.upper()}"


def folder(root: Path, name: str) -> tuple[Path, int]:
    """The Resource's folder under `root`, made with its Slot files, and its Slot count."""
    slots = counts().get(name, 1)
    place = root / name
    (place / "waiting").mkdir(parents=True, exist_ok=True)
    for i in range(slots):
        (place / f"{i}.lock").touch()
    return place, slots


def _locked(path: Path) -> bool:
    """Whether some process holds `path` locked (a quick try, released at once)."""
    try:
        with path.open() as f:  # never "a": that would make a note just removed
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
    except BlockingIOError:
        return True
    except FileNotFoundError:
        return False


def _take(path: Path) -> TextIO | None:
    f = path.open("a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except BlockingIOError:
        f.close()
        return None


@contextmanager
def hold(
    place: Path,
    slots: int,
    note: dict[str, Any],
    waits: Callable[[], None] = lambda: None,
    stop: Callable[[], bool] = lambda: False,
) -> Iterator[int]:
    """Hold one Slot of the Resource at `place` for the block; `waits` is called once when none
    is free, and `stop` between tries ends the wait with InterruptedError. `note` (run, root,
    stage, package) is what the holder and waiting notes say."""
    note = {**note, "since": time.time()}
    mine = place / "waiting" / f"{os.getpid()}-{secrets.token_hex(4)}.json"
    waiting: TextIO | None = None
    try:
        while True:
            for i in range(slots):
                if (slot := _take(place / f"{i}.lock")) is None:
                    continue
                holder = place / f"{i}.holder"
                holder.write_text(json.dumps({**note, "since": time.time()}))
                if waiting:
                    waiting.close()
                    mine.unlink(missing_ok=True)
                    waiting = None
                try:
                    yield i
                finally:
                    holder.unlink(missing_ok=True)  # while still held, so never another's note
                    slot.close()
                return
            if waiting is None and (waiting := _take(mine)):
                waiting.write(json.dumps(note))
                waiting.flush()
                waits()
            time.sleep(0.5)
            if stop():
                raise InterruptedError(f"stopped while waiting for {place.name}")
    finally:
        if waiting:
            waiting.close()
            mine.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        said = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return said if isinstance(said, dict) else None


def snapshot(root: Path) -> list[dict[str, Any]]:
    """Every Resource under `root`: its Slots with their live holders, and its live waiters,
    longest waiting first. Stale notes (their file not locked) are left out."""
    out: list[dict[str, Any]] = []
    for place in sorted(p for p in root.glob("*") if p.is_dir() and NAME.match(p.name)):
        n = max(counts().get(place.name, 1), len(list(place.glob("*.lock"))))
        held = [_locked(place / f"{i}.lock") for i in range(n)]
        slots = [
            {"slot": i, "held": on, "holder": _read(place / f"{i}.holder") if on else None}
            for i, on in enumerate(held)
        ]
        waiting = [w for f in place.glob("waiting/*.json") if _locked(f) and (w := _read(f))]
        waiting.sort(key=lambda w: w.get("since", 0))
        out.append({"name": place.name, "slots": slots, "waiting": waiting})
    return out


def waits(root: Path) -> list[dict[str, Any]]:
    """Each live waiter with the Resource it waits for and that Resource's holders."""
    out = []
    for r in snapshot(root):
        holders = [s["holder"] for s in r["slots"] if s["holder"]]
        out += [{**w, "resource": r["name"], "holders": holders} for w in r["waiting"]]
    return out
