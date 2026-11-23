"""Review Feeds (review_feeds.py): a package's own app says what waits on the operator, and Needs
you shows it, by kind and naming the run that made it, with one push per new item. The feed is a
fake: no network."""

import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from api_fakes import install, until
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_push_http import AUTH, P256DH, PushService

from kriflow_api import main, review_feeds
from kriflow_api.main import create_app
from kriflow_api.review_feeds import ReviewFeeds

TRAY = "packages/connectors/tests/fixtures/packages/tray"
OPEN = "https://tray.example:8765/#/review"
DAILY = "name: Daily cut\nstages:\n  - gate: ok\n    ask: Fine?\n"


def item(id: str, kind: str = "move", run_id: str | None = None) -> dict[str, Any]:
    return {"id": id, "kind": kind, "title": f"Thing {id}", "run_id": run_id}


class Feed:
    """The package's app: `answer` is what it says, or an exception it raises."""

    def __init__(self) -> None:
        self.answer: Any = {"open_url": OPEN, "items": []}
        self.asked: list[str] = []

    def __call__(self, url: str) -> Any:
        self.asked.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def feeds(feed: Feed, urls: tuple[str, ...] = ("http://t/f",)) -> ReviewFeeds:
    worlds = SimpleNamespace(list_all=lambda: [SimpleNamespace(id="w1")])
    registry = SimpleNamespace(review_feeds=lambda _w: [("tray", "Tray things", u) for u in urls])
    # each run's workflow name; a second run of Daily cut, and runs of five more workflows
    names = {"run_1": "Daily cut", "run_2": "Daily cut"}
    names |= {f"run_w{i}": f"Flow {i}" for i in range(5)}
    return ReviewFeeds(worlds, registry, lambda w, r: names.get(r) if w == "w1" else None, feed)  # type: ignore[arg-type]


def test_a_notice_counts_by_kind_names_the_run_and_goes_at_zero() -> None:
    feed = Feed()
    f = feeds(feed)
    feed.answer["items"] = [
        item("a", "move", "run_1"),
        item("b", "move", "run_1"),
        item("c", "prop"),
        {"kind": "move"},  # no id: not an item
    ]
    f.refresh()
    (notice,) = f.notices()
    assert notice.text == "3 Tray things need you: 2 moves, 1 prop. 2 from Daily cut."
    assert (notice.kind, notice.world_id, notice.url) == ("feed", "w1", OPEN)
    assert f.for_run("w1", {"run_1"})[0].count == 2
    assert f.for_run("w2", {"run_1"}) == []
    feed.answer = {"open_url": OPEN, "items": []}
    f.refresh()
    assert f.notices() == []


def test_a_notice_groups_runs_by_workflow_caps_the_list_and_names_kinds_in_plural() -> None:
    feed = Feed()
    f = feeds(feed)
    runs = ["run_1", "run_2", "run_2", *[f"run_w{i}" for i in (0, 0, 1, 2, 3, 4)], "run_gone"]
    kinds = ["prop"] * 7 + ["character"] * 2 + ["box"]
    pairs = enumerate(zip(kinds, runs, strict=True))
    feed.answer["items"] = [item(str(n), k, r) for n, (k, r) in pairs]
    f.refresh()
    (notice,) = f.notices()
    # two runs of Daily cut are one workflow; a run whose workflow is unknown is not named
    assert notice.text == (
        "10 Tray things need you: 7 props, 2 characters, 1 box."
        " 3 from Daily cut, 2 from Flow 0, 1 from Flow 1 and 3 more."
    )


def test_a_push_goes_once_per_new_item_and_never_again() -> None:
    feed = Feed()
    f = feeds(feed)
    feed.answer["items"] = [item("a")]
    assert f.refresh() is None  # the first answer is the baseline: a restart pushes nothing
    feed.answer["items"] = [item("a"), item("b")]
    assert f.refresh() == "Tray things: Thing b needs you."
    assert f.refresh() is None
    feed.answer = RuntimeError("down")
    assert f.refresh() is None
    feed.answer = {"open_url": OPEN, "items": [item("a"), item("b"), item("c"), item("d")]}
    assert f.refresh() == "2 new Tray things need you."  # one push a pass, b not again


def test_a_feed_down_or_talking_nonsense_shows_nothing_and_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    feed = Feed()
    f = feeds(feed)
    feed.answer["items"] = [item("a")]
    f.refresh()
    feed.answer = TimeoutError("slow")
    caplog.set_level(logging.WARNING)
    for _ in range(3):
        assert f.refresh() is None
        assert f.notices() == []
    assert caplog.text.count("review feed http://t/f unreadable") == 1
    feed.answer = {"open_url": "javascript:alert(1)", "items": "nope"}
    assert f.refresh() is None and f.notices() == []
    feed.answer = {"open_url": "javascript:alert(1)", "items": [item("a")]}
    f.refresh()
    assert f.notices()[0].url is None  # only an http(s) link is opened


def test_no_feed_url_is_off() -> None:
    feed = Feed()
    f = feeds(feed, urls=())
    assert f.refresh() is None and f.notices() == [] and feed.asked == []


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> PushService:
    fake = PushService()
    monkeypatch.setattr(main.push, "sender", lambda _keys: fake)
    monkeypatch.setattr(main, "PUSH_NOTICE_SECONDS", 0.05)
    return fake


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    fake = Feed()
    monkeypatch.setattr(review_feeds, "fetch", fake)
    return fake


@pytest.fixture
def running(tmp_path: Path, service: PushService, feed: Feed) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path, retro=False)) as client:
        yield client


def test_a_package_feed_reaches_needs_you_the_run_page_and_the_phone_once(
    running: TestClient, service: PushService, feed: Feed
) -> None:
    assert install(running, TRAY).status_code == 201
    world = running.post("/api/worlds", json={"name": "Shorts"})
    world = world.json()["id"]
    assert running.put(f"/api/worlds/{world}/packages/tray", json={"version": "1.0.0"}).is_success
    assert feed.asked == []  # its URL not set yet: off, nothing fetched
    body = {"endpoint": "https://push.example/phone", "keys": {"p256dh": P256DH, "auth": AUTH}}
    assert running.post("/api/push/subscriptions", json=body).status_code == 200
    feed.answer["items"] = [item("a", run_id="run_x")]
    setup = {"world": {"app_url": "http://tray:8765/"}}
    assert running.put(f"/api/worlds/{world}/packages/tray/setup", json=setup).is_success
    until(lambda: "http://tray:8765/api/review-feed" in feed.asked)
    until(lambda: any(n["kind"] == "feed" for n in running.get("/api/notices").json()))
    (notice,) = [n for n in running.get("/api/notices").json() if n["kind"] == "feed"]
    assert notice == {
        "kind": "feed",
        "text": "1 Tray things need you: 1 move.",
        "world_id": world,
        "run_id": None,
        "url": OPEN,
    }
    mine = running.get(f"/api/worlds/{world}/runs/run_x/review-feed").json()
    assert mine == [{"label": "Tray things", "count": 1, "open_url": OPEN}]

    feed.answer = {"open_url": OPEN, "items": [item("a", run_id="run_x"), item("b")]}
    until(lambda: len(service.sent) == 1)
    asked = len(feed.asked)
    until(lambda: len(feed.asked) > asked + 3)  # several passes later, still one push
    ((_, payload),) = service.sent
    # a tap opens Needs you with the feed's notice and its Open link in focus
    assert payload["body"] == "Tray things: Thing b needs you."
    assert payload["url"] == "/gates?notice=feed"


def test_a_feed_notice_names_a_runs_workflow_and_never_an_ad_hoc_runs_goal(
    service: PushService, feed: Feed, app: FastAPI
) -> None:
    """The app's own lookup: two runs' items group under their workflow's name; an Ad Hoc Run
    (named after its free-text goal) is counted but never named."""
    with TestClient(app) as client:
        assert install(client, TRAY).status_code == 201
        world = client.post("/api/worlds", json={"name": "Shorts"}).json()["id"]
        package = f"/api/worlds/{world}/packages/tray"
        assert client.put(package, json={"version": "1.0.0"}).is_success
        made = client.post(f"/api/worlds/{world}/workflows", json={"yaml": DAILY})
        assert made.status_code == 201, made.json()
        runs = [
            client.post(f"/api/workflows/{made.json()['workflow_id']}/runs", json={}).json()["id"]
            for _ in range(2)
        ]
        goal = "Smoke test of the makers (no short is made). Use the tools only."
        ad_hoc = client.post(f"/api/worlds/{world}/runs", json={"goal": goal}).json()["id"]
        feed.answer["items"] = [item("a", "prop", runs[0]), item("b", "prop", runs[1])]
        feed.answer["items"] += [item("c", "prop", ad_hoc)]
        setup = {"world": {"app_url": "http://tray:8765/"}}
        assert client.put(f"{package}/setup", json=setup).is_success

        def text() -> str | None:
            found = [n for n in client.get("/api/notices").json() if n["kind"] == "feed"]
            return found[0]["text"] if found else None

        until(lambda: text() is not None)
        assert text() == "3 Tray things need you: 3 props. 2 from Daily cut."
