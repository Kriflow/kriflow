"""A stop mid-publish never posts twice (Safe Deploys): the app is stopped while the platform is
taking the video's bytes, then started again on the same data folder. The fake_publish fixture
uploads resumably, keeping a Publish Record as a real package does (scripted_app.py), to a fake
platform served here, outside the app, so the platform outlives the restart. Cut (no drain), the
resumed call finishes the same upload from its Publish Record, or asks "Did this post go out?";
drained, the stage finishes before the stop and is never called again. Either way the platform holds
exactly one video."""

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from kriflow_connectors.setup import SetupStore

FLOW = """name: Post once
stages:
  - swarm: research
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
    produces: [ideas.md]
  - connector: render
    calls: kit
    input: ideas.md
    output: short.mp4
    params: {project: shorts, composition: Short}
  - connector: publish
    calls: fake_publish
    input: short.mp4
    params: {title: One post, visibility: private}
"""
SESSION = "/session/s1"


class Platform(ThreadingHTTPServer):
    """A platform's resumable upload, enough of it: one session that
    keeps the bytes sent and becomes a video once whole. The first bytes request takes half the
    file, sets `began`, then holds until `release`: dropped, or with `finish` taking the rest."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.size, self.got, self.sessions = 0, b"", 0
        self.videos: list[str] = []
        self.held = False
        self.began, self.release = threading.Event(), threading.Event()
        self.finish = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    @property
    def platform(self) -> Platform:
        return cast(Platform, self.server)

    def answer(
        self, status: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> None:
        data = json.dumps(body or {}).encode()
        self.send_response(status)
        for k, v in {**(headers or {}), "Content-Type": "application/json"}.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # resumable: a session
        g = self.platform
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        g.size, g.got, g.sessions = (
            int(self.headers["X-Upload-Content-Length"]),
            b"",
            g.sessions + 1,
        )
        self.answer(200, headers={"Location": SESSION})

    def do_PUT(self) -> None:  # the bytes, or where the session stands
        g = self.platform
        length = int(self.headers.get("Content-Length") or 0)
        span = self.headers.get("Content-Range", "")
        if span.startswith("bytes */"):
            if g.videos:
                self.answer(200, {"id": g.videos[-1]})
            else:
                self.answer(308, headers={"Range": f"bytes=0-{len(g.got) - 1}"} if g.got else {})
            return
        start = int(span.split()[1].split("-")[0]) if span else 0
        if not g.held:
            g.held = True
            half = length // 2
            g.got = g.got[:start] + self.rfile.read(half)
            g.began.set()
            assert g.release.wait(60), "never released"
            if not g.finish:  # the app is gone: the rest never arrives
                self.close_connection = True
                return
            g.got += self.rfile.read(length - half)
        else:
            g.got = g.got[:start] + self.rfile.read(length)
        if len(g.got) < g.size:
            self.answer(308, headers={"Range": f"bytes=0-{len(g.got) - 1}"})
            return
        g.videos.append(f"vid{len(g.videos) + 1}")
        self.answer(200, {"id": g.videos[-1]})


@pytest.fixture
def platform() -> Iterator[Platform]:
    server = Platform()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.release.set()
    server.shutdown()


def wait(check: Callable[[], object], seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def down(base_url: str) -> bool:
    try:
        httpx.get(f"{base_url}/api/health", timeout=1)
    except httpx.HTTPError:
        return True
    return False


def start_run(base_url: str, data_dir: Path) -> str:
    # fake_publish signed in, as the Connectors page leaves it
    store = SetupStore(data_dir / "setup.json")
    store.put_value("fake_publish", None, "account_client_id", "id")
    store.put_value("fake_publish", None, "account_client_secret", "s")
    tokens = {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 3600}
    store.put_value("fake_publish", None, "account", tokens)
    api = httpx.Client(base_url=f"{base_url}/api")
    world = api.post("/worlds", json={"name": "Posts"}).json()["id"]
    made = api.post(f"/worlds/{world}/workflows", json={"yaml": FLOW})
    assert made.status_code < 300, made.text
    return str(api.post(f"/workflows/{made.json()['workflow_id']}/runs", json={}).json()["id"])


def settled(base_url: str, run: str) -> tuple[str, dict[str, Any]]:
    """The run's status once it is done, failed or waiting at a gate, and its publish call."""
    api = httpx.Client(base_url=f"{base_url}/api")
    wait(lambda: api.get(f"/runs/{run}").json()["status"] not in ("queued", "running"))
    ended = api.get(f"/runs/{run}").json()
    (stage,) = [s["id"] for s in ended["stages"] if s["name"] == "publish"]
    return ended["status"], api.get(f"/stages/{stage}/call").json()


def test_a_stop_cutting_a_publish_resumes_it_and_posts_once(
    serve: Callable[..., AbstractContextManager[str]], platform: Platform, tmp_path: Path
) -> None:
    with serve(
        tmp_path, SWARMIFY_E2E_PLATFORM=platform.url, SWARMIFY_DRAIN_SECONDS="0"
    ) as base_url:
        run = start_run(base_url, tmp_path)
        assert platform.began.wait(60), "the upload never began"
    # The app is stopped (SIGTERM) and gone, half the file at Platform, no video yet.
    assert platform.videos == [] and 0 < len(platform.got) < platform.size
    platform.release.set()

    with serve(tmp_path, SWARMIFY_E2E_PLATFORM=platform.url) as base_url:
        status, call = settled(base_url, run)
        if status == "waiting_gate":  # unsure: asks, never posts blind
            gates = httpx.get(f"{base_url}/api/gates").json()
            assert [g["kind"] for g in gates if g["run_id"] == run] == ["posted"]
            assert platform.videos == []
        else:  # resumed: the same upload session finished, under the same key
            assert status == "done", call
            assert (call["attempts"], call["response"]["url"]) == (
                2,
                "https://fake.example.org/v/vid1",
            )
            assert platform.videos == ["vid1"]
    assert platform.sessions == 1


def test_a_stop_drains_a_publish_at_work_so_it_finishes_and_is_never_called_again(
    serve: Callable[..., AbstractContextManager[str]], platform: Platform, tmp_path: Path
) -> None:
    with serve(
        tmp_path, SWARMIFY_E2E_PLATFORM=platform.url, SWARMIFY_DRAIN_SECONDS="30"
    ) as base_url:
        run = start_run(base_url, tmp_path)
        assert platform.began.wait(60), "the upload never began"

        def finish_once_stopping() -> None:  # Platform takes the rest once the app is stopping
            wait(lambda: down(base_url))
            platform.finish = True
            platform.release.set()

        threading.Thread(target=finish_once_stopping, daemon=True).start()
    # Stopped: the drain held the stop until the stage finished.
    assert platform.videos == ["vid1"]

    with serve(tmp_path, SWARMIFY_E2E_PLATFORM=platform.url) as base_url:
        status, call = settled(base_url, run)
        assert status == "done", call
        assert (call["attempts"], call["response"]["url"]) == (1, "https://fake.example.org/v/vid1")
        assert httpx.get(f"{base_url}/api/gates").json() == []
    assert platform.videos == ["vid1"] and platform.sessions == 1
