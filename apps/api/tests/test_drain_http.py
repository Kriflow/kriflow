"""The Drain over the real app (Safe Deploys): a stop waits for the stages at work, and open
event streams cannot hold the stop past uvicorn's graceful-shutdown timeout. Also pins what an
interrupted publish leaves today (the spec's open questions 3 and 4)."""

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from api_fakes import sign_in_fake_publish
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_publish_http import setup, until, weekly

from kriflow_api.main import create_app, run_storage
from kriflow_connectors import FakeTransport
from kriflow_runs import Run, StageDef

ONE_STAGE = "name: Render\nstages:\n  - connector: render\n    calls: maker\n"


@pytest.mark.parametrize("graceful", [None, 1])
def test_an_open_event_stream_holds_a_stop_unless_uvicorn_cuts_it(
    app: FastAPI, graceful: int | None
) -> None:
    """Question 4: without `--timeout-graceful-shutdown` a phone's open event stream keeps
    uvicorn from ever reaching the lifespan's shutdown, so on the box Docker's kill came first
    and no drain could run. With it, the stream is cut and the shutdown runs."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=graceful
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    until(lambda: server.started)
    port = server.servers[0].sockets[0].getsockname()[1]
    with httpx.stream("GET", f"http://127.0.0.1:{port}/api/events", timeout=30) as stream:
        assert stream.status_code == 200
        server.should_exit = True  # what uvicorn's SIGTERM handler does
        thread.join(4)
        assert thread.is_alive() is (graceful is None)
    thread.join(10)  # the client went away: the held stop ends too
    assert not thread.is_alive()


def test_a_stop_waits_for_the_stage_at_work_and_nothing_runs_twice(
    tmp_path: Path, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KRIFLOW_DRAIN_SECONDS", "30")
    release, called = threading.Event(), []

    def slow(run: Run, stage_id: str, stage: StageDef) -> None:
        called.append(stage.name)
        release.wait(20)

    def app() -> FastAPI:
        return create_app(tmp_path, connector=slow, transport=transport, retro=False)

    client = TestClient(app())
    client.__enter__()
    _, workflow = setup(client, ONE_STAGE)
    run = client.post(f"/api/workflows/{workflow}/runs", json={}).json()
    until(lambda: called == ["render"])
    stopping = threading.Thread(target=client.__exit__, args=(None, None, None))
    stopping.start()
    stopping.join(3)
    assert stopping.is_alive()  # draining: the stage is at work
    release.set()
    stopping.join(20)
    assert not stopping.is_alive()

    with TestClient(app()) as again:
        until(lambda: again.get(f"/api/runs/{run['id']}").json()["status"] == "done")
    assert called == ["render"]  # finished before the stop, so never replayed


def test_a_sandbox_stopped_mid_publish_fails_the_stage_while_the_api_lives(
    app: FastAPI, transport: FakeTransport, tmp_path: Path
) -> None:
    """Question 3: when the sandbox (the connector's process) dies first, the live api records
    the call failed and fails the run at once; nothing replays it. Only an api that dies first
    leaves the call running in the ledger, which the next launch replays (and a publish then
    refuses, packages/connectors/tests/test_publish.py). So the deploy command stops the api
    before the sandbox."""
    sign_in_fake_publish(tmp_path)
    yaml = weekly().replace("  - gate: confirm\n    ask: Post it?\n    kind: confirm\n", "")
    yaml = yaml.replace("visibility: public", "visibility: private").replace(
        '      schedule: "2099-01-01T10:00:00+01:00"\n', ""
    )
    with TestClient(app) as client:
        _, workflow = setup(client, yaml)

        def render(args: dict) -> dict:
            Path(args["output_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(args["output_path"]).write_bytes(b"video")
            return {"ok": True}

        transport.outputs["maker.maker"] = [render]
        transport.outputs["publish"] = [ConnectionResetError("the sandbox went away")]
        run = client.post(f"/api/workflows/{workflow}/runs", json={}).json()
        until(lambda: client.get(f"/api/runs/{run['id']}").json()["status"] in ("done", "failed"))
    ended = run_storage(tmp_path).get_run(run["id"])
    assert ended is not None and ended.status == "failed"
    assert "the sandbox went away" in (ended.stages[-1].error or "")
    assert [c[1] for c in transport.calls].count("publish") == 1


PASSWORD = "aviato"


@pytest.fixture
def guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: FakeTransport) -> FastAPI:
    monkeypatch.setenv("KRIFLOW_PASSWORD", PASSWORD)
    return create_app(tmp_path / "data", retro=False, transport=transport)


def test_a_drain_is_started_read_and_cancelled_and_the_needs_you_page_says_so(
    client: TestClient, transport: FakeTransport
) -> None:
    with client:
        assert client.get("/api/drain").json() == {
            "state": "serving",
            "seconds_left": None,
            "at_work": [],
        }
        assert client.get("/api/notices").json() == []
        started = client.post("/api/drain", json={"seconds": 600}).json()
        assert started["state"] == "drained" and 590 < started["seconds_left"] <= 600
        assert client.post("/api/drain").json()["seconds_left"] <= 600  # keeps its limit
        (notice,) = client.get("/api/notices").json()
        assert notice["kind"] == "updating"
        assert notice["text"].startswith("Updating. Stages already at work finish first")

        _, workflow = setup(client, ONE_STAGE)
        run = client.post(f"/api/workflows/{workflow}/runs", json={}).json()
        time.sleep(0.5)
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "queued"
        assert client.delete("/api/drain").json()["state"] == "serving"
        until(lambda: client.get(f"/api/runs/{run['id']}").json()["status"] == "done")
        assert client.get("/api/notices").json() == []


def test_a_drain_needs_a_session_or_a_token_of_the_whole_server(guarded: FastAPI) -> None:
    with TestClient(guarded) as client:
        assert client.get("/api/drain").status_code == 401
        assert client.post("/api/session", json={"password": PASSWORD}).status_code == 204
        world = client.post("/api/worlds", json={"name": "W"}).json()["id"]
        full = client.post("/api/tokens", json={"name": "deploy"}).json()["token"]
        one = client.post("/api/tokens", json={"name": "w", "world_id": world}).json()["token"]
        assert client.get("/api/drain").status_code == 200
        client.cookies.clear()
        assert client.get("/api/drain").status_code == 401
        bearer = {"Authorization": f"Bearer {full}"}
        assert client.get("/api/drain", headers=bearer).status_code == 200
        assert client.get("/api/worlds", headers=bearer).status_code == 401  # still only here
        refused = client.get("/api/drain", headers={"Authorization": f"Bearer {one}"})
        assert refused.status_code == 401


def test_the_console_script_drives_the_drain_with_a_token(
    guarded: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from kriflow_api import drain

    server = uvicorn.Server(uvicorn.Config(guarded, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        until(lambda: server.started)
        url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
        monkeypatch.setenv("KRIFLOW_DRAIN_URL", url)
        with pytest.raises(SystemExit, match="401"):
            drain.main(["status"])
        session = httpx.post(f"{url}/api/session", json={"password": PASSWORD})
        made = httpx.post(f"{url}/api/tokens", json={"name": "deploy"}, cookies=session.cookies)
        monkeypatch.setenv("KRIFLOW_API_TOKEN", made.json()["token"])

        def said(*args: str) -> dict:
            drain.main(list(args))
            return json.loads(capsys.readouterr().out)

        assert said("start", "--seconds", "90")["state"] == "drained"
        assert said("status")["seconds_left"] <= 90
        assert said("cancel")["state"] == "serving"
    finally:
        server.should_exit = True
        thread.join(10)
