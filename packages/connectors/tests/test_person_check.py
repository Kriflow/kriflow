"""Person Checks through their files: a request in a process's scratch folder opens one, its
screenshot is copied only from that folder, an answer lands mode 600 beside the request, a code
is a secret of the stage, and Stop or no answer in time fails the stage."""

import json
import stat
import time
from pathlib import Path

import pytest

from swarmify_connectors.person_check import STOPPED, TIMED_OUT, PersonChecks
from swarmify_kernel import Event, scrub

STAGE = ("run_1", "stg_1")


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def checks(tmp_path: Path, events: list[Event]) -> PersonChecks:
    return PersonChecks(tmp_path / "data", events.append, timeout=30, poll=0.02)


def ask(home: Path, ask_id: str, kind: str = "code", screenshot: str = "shot.png") -> None:
    (home / "asks").mkdir(parents=True, exist_ok=True)
    (home / "shot.png").write_bytes(b"\x89PNG fake")
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
    assert check.screenshot and check.screenshot.read_bytes() == b"\x89PNG fake"
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
