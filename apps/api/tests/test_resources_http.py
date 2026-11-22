"""Resources through the API (ADR 0030): a package asking the core to `hold` a one-seat service
around each stage call runs one stage at a time across runs; the waiting stage says who holds
the seat, and GET /api/resources lists the holder and the waiter."""

import threading
from pathlib import Path

from api_fakes import install, until
from fastapi.testclient import TestClient

from kriflow_connectors import FakeTransport

MANIFEST = """name = "seated"
version = "1.0.0"
label = "Seated"
about = "Calls a service with one seat. A fixture."
kind = "stage"
stage_types = ["connector"]
runtime = "uv"
command = ["python3", "server.py"]

[permissions]
hold = ["seat"]
"""
FLOW = """name: Translate notes
stages:
  - connector: translate
    calls: seated
    params: {tool: work}
"""


def test_a_held_resource_runs_one_stage_at_a_time_and_the_waiting_stage_says_who_holds_it(
    client: TestClient, transport: FakeTransport, tmp_path: Path
) -> None:
    source = tmp_path / "seated"
    source.mkdir()
    (source / "swarmify-connector.toml").write_text(MANIFEST)
    (source / "server.py").write_text("")
    started, release = threading.Semaphore(0), threading.Event()

    def work(_args: dict) -> dict:
        started.release()
        assert release.wait(30)
        return {"ok": True}

    transport.outputs["work"] = [work]
    with client:
        assert install(client, str(source)).status_code == 201
        w = client.post("/api/worlds", json={"name": "Notes"}).json()["id"]
        assert client.put(f"/api/worlds/{w}/packages/seated", json={"version": "1.0.0"}).is_success
        flow = client.post(f"/api/worlds/{w}/workflows", json={"yaml": FLOW}).json()["workflow_id"]
        first = client.post(f"/api/workflows/{flow}/runs", json={}).json()
        assert started.acquire(timeout=30)
        second = client.post(f"/api/workflows/{flow}/runs", json={}).json()

        def waiting() -> str | None:
            return client.get(f"/api/runs/{second['id']}").json()["stages"][0].get("waiting")

        until(lambda: waiting() is not None)
        run = client.get(f"/api/runs/{first['id']}").json()
        assert waiting() == f"waiting for seat: held by {run['name']} / translate"
        [seat] = client.get("/api/resources").json()
        holders = [s["holder"] for s in seat["slots"] if s["holder"]]
        assert [(h["run"], h["stage"], h["package"]) for h in holders] == [
            (first["id"], run["stages"][0]["id"], "seated")
        ]
        assert [w["run"] for w in seat["waiting"]] == [second["id"]]
        assert not started.acquire(timeout=0.5)  # the second call has not begun

        release.set()
        for r in (first, second):
            until(lambda r=r: client.get(f"/api/runs/{r['id']}").json()["status"] == "done")
        assert client.get(f"/api/runs/{second['id']}").json()["stages"][0].get("waiting") is None
        [seat] = client.get("/api/resources").json()
        assert seat["waiting"] == [] and not any(s["holder"] for s in seat["slots"])
