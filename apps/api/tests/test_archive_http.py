"""Archive and restore over HTTP: a workflow hides from every list, stops its schedule and
refuses new runs until restored; a finished run (with its child runs) hides from the runs list
and still opens by link. Real DBOS on SQLite behind the app."""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from api_fakes import FakeNotifier, until
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kriflow_api.main import run_control, run_storage, task_board, workflow_service, world_service
from kriflow_api.mcp import server
from kriflow_kernel.events import read_after
from kriflow_kernel.sqlite import connect
from kriflow_runs import RunControl

GATED = "name: Gated\nstages:\n  - gate: ok\n    ask: Fine?\n"
WEEKLY = "name: Weekly\nschedule: '0 8 * * 1'\nstages:\n  - gate: ok\n    ask: Fine?\n"


@pytest.fixture
def running(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def _world(client: TestClient) -> str:
    return client.post("/api/worlds", json={"name": "Video"}).json()["id"]


def _workflow(client: TestClient, world: str, yaml: str = GATED) -> str:
    created = client.post(f"/api/worlds/{world}/workflows", json={"yaml": yaml})
    assert created.status_code == 201, created.text
    return created.json()["workflow_id"]


def _types(tmp_path: Path, world: str) -> list[str]:
    with closing(connect(tmp_path / "swarmify.db")) as conn:
        return [e.type for e in read_after(conn, world, 0)]


def test_an_archived_workflow_hides_refuses_runs_and_restores(
    running: TestClient, tmp_path: Path
) -> None:
    world = _world(running)
    wf, other = _workflow(running, world), _workflow(running, world)
    listed = f"/api/worlds/{world}/workflows"

    archived = running.post(f"/api/workflows/{wf}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None
    assert [w["id"] for w in running.get(listed).json()] == [other]
    shown = running.get(listed, params={"archived": True}).json()
    assert [(w["id"], w["archived_at"] is not None) for w in shown] == [(wf, True), (other, False)]
    assert running.get(f"/api/workflows/{wf}").json()["archived_at"] is not None

    refused = running.post(f"/api/workflows/{wf}/runs", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Gated is archived. Restore it to run it."

    restored = running.post(f"/api/workflows/{wf}/restore")
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    assert [w["id"] for w in running.get(listed).json()] == [wf, other]
    assert running.post(f"/api/workflows/{wf}/runs", json={}).status_code == 201

    assert _types(tmp_path, world).count("workflow.archived") == 1
    assert _types(tmp_path, world).count("workflow.restored") == 1
    assert running.post("/api/workflows/wf_nope/archive").status_code == 404
    assert running.post("/api/workflows/wf_nope/restore").status_code == 404


def test_archiving_a_workflow_drops_its_schedule_until_restored_and_across_a_restart(
    app: FastAPI, tmp_path: Path
) -> None:
    control = RunControl(run_storage(tmp_path), tmp_path / "dbos.sqlite", tmp_path, FakeNotifier())
    with TestClient(app) as running:
        wf = _workflow(running, _world(running), WEEKLY)
        assert wf in control.schedules()
        running.post(f"/api/workflows/{wf}/archive")
        assert wf not in control.schedules()
        # a new version of an archived workflow does not bring its schedule back
        again = running.post(f"/api/workflows/{wf}/versions", json={"yaml": WEEKLY})
        assert again.status_code == 201 and wf not in control.schedules()
        running.post(f"/api/workflows/{wf}/restore")
        assert control.schedules()[wf] == "0 8 * * 1"
        running.post(f"/api/workflows/{wf}/archive")
    control.schedule(wf, "0 8 * * 1")  # as if DBOS kept it from before the archive
    assert wf in control.schedules()
    with TestClient(app):  # the launch's sync follows the workflows table
        until(lambda: wf not in control.schedules())


@pytest.mark.anyio
async def test_the_mcp_list_hides_archived_workflows(tmp_path: Path) -> None:
    from mcp import Client

    worlds, workflows = world_service(tmp_path), workflow_service(tmp_path)
    world = worlds.create("Video").id
    live = workflows.save(world, GATED).workflow_id
    gone = workflows.save(world, GATED.replace("Gated", "Old")).workflow_id
    workflows.archive(gone)
    async with Client(server(worlds, workflows, task_board(tmp_path), run_control(tmp_path))) as c:
        listed = await c.call_tool("list_workflows", {"world_id": world})
        assert [w["id"] for w in listed.structured_content["result"]] == [live]
        both = await c.call_tool("list_workflows", {"world_id": world, "archived": True})
        assert [w["id"] for w in both.structured_content["result"]] == [live, gone]


def test_a_finished_run_archives_hides_from_the_list_opens_by_link_and_restores(
    running: TestClient, tmp_path: Path
) -> None:
    world = _world(running)
    wf = _workflow(running, world)
    run = running.post(f"/api/workflows/{wf}/runs", json={}).json()
    other = running.post(f"/api/workflows/{wf}/runs", json={}).json()
    url = f"/api/runs/{run['id']}"
    until(lambda: running.get(url).json()["status"] == "waiting_gate")

    live = running.post(f"{url}/archive")
    assert live.status_code == 409 and "finished" in live.json()["detail"]

    running.post(f"{url}/abort")
    until(lambda: running.get(url).json()["status"] == "aborted")
    archived = running.post(f"{url}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None

    runs = f"/api/worlds/{world}/runs"
    assert [r["id"] for r in running.get(runs).json()] == [other["id"]]
    shown = running.get(runs, params={"archived": True}).json()
    assert {r["id"]: r["archived_at"] is not None for r in shown} == {
        run["id"]: True,
        other["id"]: False,
    }
    assert running.get(url).json()["archived_at"] is not None  # still opens by link

    restored = running.post(f"{url}/restore")
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    assert {r["id"] for r in running.get(runs).json()} == {run["id"], other["id"]}
    assert running.post("/api/runs/run_nope/archive").status_code == 404
    assert running.post("/api/runs/run_nope/restore").status_code == 404

    with closing(connect(tmp_path / "swarmify.db")) as conn:
        archived_flags = [
            e.payload["archived_at"] is not None
            for e in read_after(conn, world, 0)
            if e.type == "run.updated" and e.run_id == run["id"]
        ]
    assert archived_flags[-2:] == [True, False]
