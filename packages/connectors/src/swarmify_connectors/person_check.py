"""Person Check (spec: World Logins And Browser Connector): a generic mid-stage gate a stage's
process opens when it needs a person, as for a code a site sent by SMS or a captcha.

The process writes a request, `{id, kind: code | look, ask, screenshot}`, as `asks/<id>.json` in
its own scratch folder (its HOME). The core watches the scratch folder of every process it starts
for a stage, opens a Person Check (Needs you and the run page show it), copies the screenshot into
the stage's folder and, once a person answers on the web, writes `asks/<id>.answer.json` (mode
600) beside the request: `{"code": "..."}`, `{"action": "try_again"}` or `{"action": "stop"}`.
The process polls for it. Stop, or TIMEOUT without an answer, also fails the stage. A typed code is
a secret of the stage (scrub.py), never in an event. Open checks live in memory: after a restart
the process that asked is gone, and its stage runs again.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from swarmify_connectors import safefile
from swarmify_kernel import Event, scrub

log = logging.getLogger(__name__)
TIMEOUT = 600.0
"""Seconds a check waits for a person before it fails its stage."""
ASKS = "asks"
SHOT_BYTES = 10 * 1024 * 1024
REQUEST_BYTES = 64 * 1024
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
"""A request's id, which names its answer file: a plain name, never a path."""
PNG, JPEG = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"
KINDS = ("code", "look")
STOPPED, TIMED_OUT = "You stopped the sign-in check", "Nobody answered the sign-in check"


@dataclass
class PersonCheck:
    id: str
    world_id: str
    run_id: str
    stage_id: str
    package: str
    kind: str
    """`code`: the person types the code the site sent; `look`: Try again or Stop."""
    ask: str
    opened_at: float
    screenshot: Path | None = None
    """The copy in the stage's folder, None when the process sent none."""
    site: str | None = None
    """The login's first domain, when the stage names one: what the person signs in to."""
    asked: str = field(default="", repr=False)
    """The request's own id, which names the answer file."""
    home: Path = field(default=Path(), repr=False)


@dataclass
class _Watched:
    world_id: str
    stage: tuple[str, str]
    package: str
    login: str | None
    site: str | None
    seen: set[str] = field(default_factory=set)


class PersonChecks:
    def __init__(
        self,
        data_dir: Path,
        emit: Callable[[Event], None],
        timeout: float | None = None,
        poll: float = 0.25,
    ) -> None:
        self.data_dir, self.emit, self.poll = data_dir, emit, poll
        self.timeout = TIMEOUT if timeout is None else timeout
        self._lock = threading.Lock()
        self._watched: dict[Path, _Watched] = {}
        self._open: dict[str, PersonCheck] = {}
        self._failed: dict[str, str] = {}
        self._thread: threading.Thread | None = None

    def watch(
        self,
        home: Path,
        world_id: str,
        stage: tuple[str, str],
        package: str,
        login: str | None = None,
        site: str | None = None,
    ) -> None:
        """Watch a stage process's scratch folder (its HOME) for requests."""
        with self._lock:
            self._watched[home] = _Watched(world_id, stage, package, login, site)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="asks")
                self._thread.start()

    def unwatch(self, home: Path) -> None:
        """The process ended: its open checks close, as nobody can answer them now."""
        with self._lock:
            self._watched.pop(home, None)
            gone = [c for c in self._open.values() if c.home == home]
        for check in gone:
            self._close(check, "the stage moved on")

    def unwatch_stage(self, stage_id: str) -> None:
        with self._lock:
            homes = [h for h, w in self._watched.items() if w.stage[1] == stage_id]
        for home in homes:
            self.unwatch(home)

    def open_checks(
        self, world_id: str | None = None, run_id: str | None = None
    ) -> list[PersonCheck]:
        with self._lock:
            found = list(self._open.values())
        return sorted(
            (c for c in found if world_id in (None, c.world_id) and run_id in (None, c.run_id)),
            key=lambda c: c.opened_at,
        )

    def get(self, check_id: str) -> PersonCheck | None:
        with self._lock:
            return self._open.get(check_id)

    def failure(self, stage_id: str) -> str | None:
        """Why a check failed the stage (Stop, or nobody answered), once; None when none did."""
        with self._lock:
            return self._failed.pop(stage_id, None)

    def answer(self, check_id: str, code: str | None = None, action: str | None = None) -> None:
        """A person's answer: a code, or `try_again` or `stop`. Raises KeyError for a check that
        is not open, ValueError for an answer that does not fit it."""
        check = self.get(check_id)
        if check is None:
            raise KeyError(check_id)
        if action == "stop":
            self._reply(check, {"action": "stop"})
            self._fail(check, STOPPED, "stopped")
            return
        if check.kind == "code":
            code = (code or "").strip()
            if not code or len(code) > 64 or "\n" in code:
                raise ValueError("Code: type the code the site sent, on one line")
            # the code is a secret of the stage from now on: never in an event or a log
            with self._lock:
                w = self._watched.get(check.home)
            label = (w.login if w else None) or "code"
            scrub.add(check.stage_id, check.run_id, label, [code], world_id=check.world_id)
            self._reply(check, {"code": code})
            self._close(check, "code given")
            return
        if action != "try_again":
            raise ValueError("Answer: Try again or Stop")
        self._reply(check, {"action": "try_again"})
        self._close(check, "try again")

    # The watcher

    def _loop(self) -> None:
        while True:
            with self._lock:
                watched = dict(self._watched)
                if not watched and not self._open:
                    self._thread = None
                    return
            for home, w in watched.items():
                try:
                    self._scan(home, w)
                except Exception:  # one bad request never stops the watcher
                    log.exception("could not read a Person Check request in %s", home)
            now = time.time()
            for check in self.open_checks():
                if now - check.opened_at > self.timeout:
                    self._reply(check, {"action": "stop"})
                    self._fail(check, TIMED_OUT, "nobody answered")
            time.sleep(self.poll)

    def _scan(self, home: Path, w: _Watched) -> None:
        """New requests in the process's `asks/`, a plain folder (never a link), each a regular
        file read without following a link, its id a plain name."""
        try:
            with safefile.folder(home, ASKS) as at:
                names = sorted(os.listdir(at))
                fresh = [
                    n for n in names
                    if n.endswith(".json") and not n.endswith(".answer.json") and n not in w.seen
                ]  # fmt: skip
                requests = []
                for name in fresh:
                    w.seen.add(name)
                    try:
                        requests.append(
                            (name, json.loads(safefile.read_at(at, name, REQUEST_BYTES)))
                        )
                    except (OSError, ValueError):
                        log.warning("refused a Person Check request %s in %s", name, home)
        except (FileNotFoundError, safefile.Unsafe):
            return
        for name, request in requests:
            asked = request.get("id") if isinstance(request, dict) else None
            asked = asked if asked is not None else name.removesuffix(".json")
            if not isinstance(asked, str) or not ID.fullmatch(asked):
                log.warning("refused a Person Check request with id %r in %s", asked, home)
                continue
            kind = request.get("kind") if request.get("kind") in KINDS else "look"
            run_id, stage_id = w.stage
            check = PersonCheck(
                f"pc_{secrets.token_hex(8)}",
                w.world_id,
                run_id,
                stage_id,
                w.package,
                str(kind),
                scrub.text(str(request.get("ask") or "The site asks for you."), stage_id)[:500],
                time.time(),
                site=w.site,
                asked=asked,
                home=home,
            )
            check.screenshot = self._copy(home, request.get("screenshot"), check)
            with self._lock:
                self._open[check.id] = check
            self._event("person_check.opened", check, kind=check.kind, ask=check.ask)

    def _copy(self, home: Path, named: object, check: PersonCheck) -> Path | None:
        """The screenshot, copied into the stage's folder when it is a PNG or JPEG regular file
        inside the process's own scratch folder, read once from a descriptor opened without
        following a link, and written into a `person-check` folder that is no link either: a
        request can name no other file of the box, nor send the copy anywhere else."""
        if not isinstance(named, str) or not named:
            return None
        path = Path(named)
        if path.is_absolute():
            for base in (home, home.resolve()):
                if path.is_relative_to(base):
                    path = path.relative_to(base)
                    break
            else:
                return None
        try:
            data = safefile.read(home, path, SHOT_BYTES)
        except (OSError, safefile.Unsafe):
            return None
        suffix = ".png" if data.startswith(PNG) else ".jpg" if data.startswith(JPEG) else None
        if suffix is None:
            return None
        runs = self.data_dir / "worlds" / check.world_id / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        where = Path(check.run_id, check.stage_id, "person-check")
        try:
            with safefile.folder(runs, where, make=True) as at:
                safefile.write(at, f"{check.id}{suffix}", data, 0o644)
        except OSError:
            log.warning("could not keep the screenshot of Person Check %s", check.id)
            return None
        return runs / where / f"{check.id}{suffix}"

    def _reply(self, check: PersonCheck, answer: dict[str, str]) -> None:
        """The answer, mode 600, beside the request: a fresh file moved into place in an `asks/`
        opened without following a link, so nothing the process planted is written through."""
        try:
            with safefile.folder(check.home, ASKS) as at:
                safefile.write(at, f"{check.asked}.answer.json", json.dumps(answer).encode())
        except OSError:  # its scratch folder is gone with the process, or is not a plain folder
            log.warning("could not answer Person Check %s", check.id)

    def _fail(self, check: PersonCheck, why: str, said: str) -> None:
        with self._lock:
            self._failed[check.stage_id] = why
        self._close(check, said)

    def _close(self, check: PersonCheck, said: str) -> None:
        with self._lock:
            if self._open.pop(check.id, None) is None:
                return
        self._event("person_check.closed", check, said=said)

    def _event(self, event: str, check: PersonCheck, /, **said: str) -> None:
        payload = {"id": check.id, "stage_id": check.stage_id, "package": check.package, **said}
        self.emit(Event.new(event, world_id=check.world_id, payload=payload, run_id=check.run_id))
