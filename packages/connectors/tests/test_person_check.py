"""Person Checks through their files: a request in a process's scratch folder opens one, its
screenshot is copied only from that folder, an answer lands mode 600 beside the request, a code
is a secret of the stage, and Stop or no answer in time fails the stage."""

import json
import os
import stat
import time
from pathlib import Path

import pytest

from kriflow_connectors.person_check import STOPPED, TIMED_OUT, PersonChecks
from kriflow_kernel import Event, scrub

STAGE = ("run_1", "stg_1")


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def checks(tmp_path: Path, events: list[Event]) -> PersonChecks:
    return PersonChecks(tmp_path / "data", events.append, timeout=30, poll=0.02)


def ask(home: Path, ask_id: str, kind: str = "code", screenshot: str = "shot.png") -> None:
    (home / "asks").mkdir(parents=True, exist_ok=True)
    (home / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    request = {
        "id": ask_id,
        "kind": kind,
        "ask": "Enter the code we sent to ...67",
        "screenshot": screenshot,
    }
    (home / "asks" / f"{ask_id}.json").write_text(json.dumps(request))


def opened(checks: PersonChecks, n: int = 1) -> list:  # type: ignore[type-arg]
    deadline = time.monotonic() + 5
    while len(found := checks.open_checks()) < n:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return found


def test_a_code_is_answered_into_a_private_file_and_kept_out_of_events(
    checks: PersonChecks, tmp_path: Path, events: list[Event]
) -> None:
    home = tmp_path / "scratch" / "home"
    checks.watch(home, "w1", STAGE, "browser", "shop_admin", "admin.myshop.com")
    ask(home, "a1")
    [check] = opened(checks)
    assert (check.kind, check.site, check.ask) == (
        "code",
        "admin.myshop.com",
        "Enter the code we sent to ...67",
    )
    assert check.screenshot and check.screenshot.read_bytes() == b"\x89PNG\r\n\x1a\nfake"
    assert "/runs/run_1/stg_1/person-check/" in str(check.screenshot)
    with pytest.raises(ValueError, match="Code"):
        checks.answer(check.id, code="")
    try:
        checks.answer(check.id, code="481516")
        answer = home / "asks" / "a1.answer.json"
        assert json.loads(answer.read_text()) == {"code": "481516"}
        assert stat.S_IMODE(answer.stat().st_mode) == 0o600
        assert scrub.text("code 481516", "stg_1") == "code [secret: shop_admin]"
    finally:
        scrub.drop("stg_1")
    assert checks.open_checks() == [] and checks.failure("stg_1") is None
    assert [e.type for e in events] == ["person_check.opened", "person_check.closed"]
    assert events[1].payload["said"] == "code given" and "481516" not in str(events)
    with pytest.raises(KeyError):
        checks.answer(check.id, code="1")  # answered once only


def test_a_request_seen_before_its_writer_finished_is_read_again(
    checks: PersonChecks, tmp_path: Path
) -> None:
    """A process's `write_text` makes the file empty, then fills it: a scan in between reads a
    request that is not whole yet, and the next scan reads it again."""
    home = tmp_path / "scratch" / "home"
    checks.watch(home, "w1", STAGE, "browser")
    (home / "asks").mkdir(parents=True)
    (home / "asks" / "a1.json").write_text('{"id": "a1", "ki')
    time.sleep(0.1)  # several scans of the half-written request
    assert checks.open_checks() == []
    ask(home, "a1")
    [check] = opened(checks)
    assert check.kind == "code"


def test_a_request_cannot_name_a_screenshot_outside_its_own_folder(
    checks: PersonChecks, tmp_path: Path
) -> None:
    home = tmp_path / "scratch" / "home"
    secret = tmp_path / "data" / "secrets.json"
    secret.parent.mkdir(parents=True)
    secret.write_text("{}")
    home.mkdir(parents=True)
    (home / "link.png").symlink_to(secret)
    checks.watch(home, "w1", STAGE, "browser")
    ask(home, "a1", screenshot=str(secret))
    ask(home, "a2", screenshot="link.png")
    ask(home, "a3", screenshot="../../data/secrets.json")
    assert [c.screenshot for c in opened(checks, 3)] == [None, None, None]


def test_try_again_answers_a_look_and_stop_fails_the_stage(
    checks: PersonChecks, tmp_path: Path
) -> None:
    home = tmp_path / "h"
    checks.watch(home, "w1", STAGE, "browser")
    ask(home, "a1", kind="look")
    [check] = opened(checks)
    with pytest.raises(ValueError, match="Try again or Stop"):
        checks.answer(check.id, code="123")
    checks.answer(check.id, action="try_again")
    assert json.loads((home / "asks" / "a1.answer.json").read_text()) == {"action": "try_again"}
    ask(home, "a2", kind="look")
    [check] = opened(checks)
    checks.answer(check.id, action="stop")
    assert checks.failure("stg_1") == STOPPED and checks.failure("stg_1") is None


def test_nobody_answering_in_time_fails_the_stage_and_an_ended_process_closes_its_checks(
    tmp_path: Path, events: list[Event]
) -> None:
    checks = PersonChecks(tmp_path / "data", events.append, timeout=0.2, poll=0.02)
    home = tmp_path / "h"
    checks.watch(home, "w1", STAGE, "browser")
    ask(home, "a1")
    opened(checks)
    deadline = time.monotonic() + 5
    while (why := checks.failure("stg_1")) is None:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert why == TIMED_OUT
    assert json.loads((home / "asks" / "a1.answer.json").read_text()) == {"action": "stop"}
    ask(home, "a2")
    opened(checks)
    checks.unwatch(home)
    assert checks.open_checks() == []


def test_planted_links_and_a_path_for_an_id_never_write_outside_asks(
    tmp_path: Path, events: list[Event]
) -> None:
    """Regression (security review of #579): a link planted as the answer or its temporary file
    is replaced, never written through, and an id that is a path is refused."""
    victim = tmp_path / "data" / "secrets.json"
    victim.parent.mkdir(parents=True)
    victim.write_text('{"logins/w/x/password": "hunter2"}')
    checks = PersonChecks(tmp_path / "data", events.append, timeout=0.2, poll=0.02)
    home = tmp_path / "data" / "sandbox" / "1-abc" / "home"
    ask(home, "x", kind="look")
    for name in ("x.answer.json.tmp", "x.answer.json", f".x.answer.json.{os.getpid()}.tmp"):
        (home / "asks" / name).symlink_to(victim)
    (home / "asks" / "y.json").write_text(json.dumps({"id": "../../../../escaped", "kind": "look"}))
    checks.watch(home, "w1", STAGE, "browser")
    deadline = time.monotonic() + 5
    while checks.failure("stg_1") is None:  # x times out and is answered stop
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert victim.read_text() == '{"logins/w/x/password": "hunter2"}'
    assert not (home / "asks" / "x.answer.json").is_symlink()
    assert json.loads((home / "asks" / "x.answer.json").read_text()) == {"action": "stop"}
    assert not list(tmp_path.rglob("escaped*"))
    assert [e.type for e in events].count("person_check.opened") == 1  # y was refused


def test_asks_as_a_link_is_not_read_and_a_screenshot_must_be_an_image_in_home(
    checks: PersonChecks, tmp_path: Path
) -> None:
    """Regression: `asks/` a link elsewhere is ignored; a screenshot that is not a PNG or JPEG,
    or a `person-check` folder the stage turned into a link, keeps no copy anywhere."""
    elsewhere = tmp_path / "elsewhere"
    ask(elsewhere, "a1")
    linked = tmp_path / "linked" / "home"
    linked.mkdir(parents=True)
    (linked / "asks").symlink_to(elsewhere / "asks")
    checks.watch(linked, "w1", ("run_1", "stg_0"), "browser")

    home = tmp_path / "h"
    ask(home, "a2")
    (home / "shot.png").write_bytes(b"not an image")
    checks.watch(home, "w1", STAGE, "browser")
    [check] = opened(checks)
    assert check.screenshot is None and check.stage_id == "stg_1"

    target = tmp_path / "target"
    target.mkdir()
    stage = tmp_path / "data" / "worlds" / "w1" / "runs" / "run_2" / "stg_2"
    stage.mkdir(parents=True)
    (stage / "person-check").symlink_to(target)
    other = tmp_path / "h2"
    ask(other, "a3")
    checks.watch(other, "w1", ("run_2", "stg_2"), "browser")
    assert opened(checks, 2)[-1].screenshot is None
    assert list(target.iterdir()) == []
