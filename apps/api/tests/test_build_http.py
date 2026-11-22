"""A script stage then a build stage of the fixture package `sketch`, end to end through the
API: real DBOS on SQLite, the Agent SDK client with a scripted CLI as the builder, and sketch's
`finish` (its render after the session) answered in-process, as are the script stage's writes.
What a real build package's own CLI does is its package's to test, in Kriflow/packages."""

import json
import os
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fake_graph import fake_extract
from swarmify_api.main import create_app
from swarmify_connectors import Connector, ConnectorError, FakeTransport
from swarmify_swarm import AgentRuntime, SdkRuntime, SessionEnd, SessionHooks, SessionSpec, build
from swarmify_swarm.fake import Answer, Call, Die, Say, Script

SCRIPT_WRITER = {
    "name": "script_writer",
    "kind": "stage",
    "server": "uvx script-mcp",
    "stage_types": ["connector"],
}


class Rendering(FakeTransport):
    """The script stage writes its script; sketch's `finish` renders the build folder it is given
    (`site`) into the stage's output, or answers as `outputs` declare (an error, a long job)."""

    def call(
        self, connector: Connector, tool: str, arguments: dict[str, Any], env: dict[str, str]
    ) -> dict[str, Any]:
        answer = super().call(connector, tool, arguments, env)  # raises a declared error
        out = arguments.get("output_path")
        if tool == "script_writer" and out:
            key = arguments.get("idempotency_key", "")
            Path(out).write_text(
                f"# Why the chase works\n\nHook, then the reveal.\n<!-- {key} -->\n"
            )
            return {"output": out}
        if tool == "finish" and out:
            site = {"site": arguments["site"], "input": arguments.get("input_path")}
            Path(out).write_text(json.dumps(site))
            return answer if "job_id" in answer else {"output": out}
        return answer


@pytest.fixture
def transport() -> Rendering:
    return Rendering()


@pytest.fixture
def opened() -> Iterator[list[TestClient]]:
    """Every app a test starts, shut down after it whatever happened, so DBOS stops."""
    clients: list[TestClient] = []
    yield clients
    for client in clients:
        client.__exit__(None, None, None)


def build_run(
    tmp_path: Path,
    runtime: AgentRuntime,
    params: str,
    opened: list[TestClient],
    transport: FakeTransport,
    inputs: str = "input: script.md",
    after: str = "",
    retro: bool = False,
    wait: bool = True,
) -> tuple[TestClient, dict]:
    """`inputs` is the build stage's input line (and output, else video.mp4); a notes stage
    writing notes.md runs before it when it names notes.md. `after` is YAML for stages after it;
    the run is returned once it ends or waits at a gate. `retro` ends the run with its Retro,
    whose session the script answers. Without `wait` the run is returned as it starts."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / ".env").write_text("EXAMPLE_KEY=k3y\n")
    (data / ".env").chmod(0o600)
    app = create_app(
        data,
        transport=transport,
        poll_seconds=0.05,
        retro=retro,
        runtime=runtime,
        extract=fake_extract,
    )
    client = TestClient(app)
    client.__enter__()
    opened.append(client)
    w = client.post("/api/worlds", json={"name": "Shorts"}).json()["id"]
    added = client.post(f"/api/worlds/{w}/connectors", json=SCRIPT_WRITER)
    assert added.status_code == 201, added.text
    notes = "  - connector: notes\n    calls: script_writer\n    output: notes.md\n"
    output = "" if "output:" in inputs else "\n    output: video.mp4"
    yaml = f"""name: Script then build
stages:
  - connector: script
    calls: script_writer
    output: script.md
{notes if "notes.md" in inputs else ""}  - connector: video
    calls: sketch
    {inputs}{output}
    params: {params}
{after}"""
    workflow = client.post(f"/api/worlds/{w}/workflows", json={"yaml": yaml})
    assert workflow.status_code == 201, workflow.text
    run = client.post(f"/api/workflows/{workflow.json()['workflow_id']}/runs", json={}).json()
    return client, settled(client, run["id"]) if wait else run


def settled(client: TestClient, run_id: str, send_backs: int = 0) -> dict:
    """The run once it ended or waits at a gate, after `send_backs` send-backs."""
    deadline = time.monotonic() + float(os.environ.get("SWARMIFY_BUILD_WAIT", 60))
    while True:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["send_backs"] == send_backs and run["status"] in ("done", "failed", "waiting_gate"):
            return run
        assert time.monotonic() < deadline, run
        time.sleep(0.1)


def _modules(options: Any) -> None:
    """What an install leaves in the build folder, which the core drops once the call is done."""
    (Path(options.cwd) / "node_modules/x").mkdir(parents=True)


def test_the_builder_writes_the_build_and_the_render_makes_the_video(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    script = Script(
        [
            Call("Bash", {"command": "npm install"}),
            Answer(_modules),
            Call("Write", {"file_path": "page.css", "content": "h1 {}"}),
            Call("Write", {"file_path": str(tmp_path / "data/.env"), "content": "X=1"}),
            Say("A title card over black."),
        ],
        cost=0.37,
    )
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{brief: Loud.}", opened, transport
    )
    assert run["status"] == "done", run
    script_id, video_id = run["stages"][0]["id"], run["stages"][1]["id"]
    folder = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"]
    project = folder / video_id / "build"
    rendered = json.loads((folder / video_id / "artifacts/video.mp4").read_text())
    assert rendered == {
        "site": str(project),
        "input": str(folder / script_id / "artifacts/script.md"),
    }

    # the session: in the build folder, sandboxed, no keys, the hooks refusing the write out
    (options,) = script.sessions
    assert options.cwd == project
    assert (options.sandbox or {}).get("allowUnsandboxedCommands") is False
    assert options.env["SWARMIFY_PASSWORD"] == ""
    store = str((tmp_path / "data/pnpm-store").resolve())  # one pnpm store for every build
    assert options.env["pnpm_config_store_dir"].endswith("/data/pnpm-store")
    filesystem: Any = (options.sandbox or {}).get("filesystem") or {}
    assert store in filesystem["allowWrite"]
    assert [o.is_error for o in script.outcomes] == [False, False, True]
    assert (tmp_path / "data/.env").read_text() == "EXAMPLE_KEY=k3y\n"
    assert "Loud." in script.prompts[0]

    # the folder stays for a person to open, its node_modules gone; the render ran after
    assert (project / "index.html").is_file() and not (project / "node_modules").exists()
    call = client.get(f"/api/stages/{video_id}/call").json()
    assert call["connector"] == "sketch" and call["request"]["site"] == str(project)
    [artifact] = client.get(f"/api/runs/{run['id']}/stages/{video_id}/artifacts").json()
    assert artifact["name"] == "video.mp4"
    assert run["spent_usd"] == pytest.approx(0.37)  # the builder's session, on the run budget


def test_a_build_with_several_inputs_renders_the_first_and_names_the_rest(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    script = Script([Say("Done.")])
    runtime = SdkRuntime(transport=script.transport)
    client, run = build_run(
        tmp_path, runtime, "{}", opened, transport, "inputs: [script.md, notes.md]"
    )
    assert run["status"] == "done", run
    script_id, notes_id, video_id = (s["id"] for s in run["stages"])
    folder = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"]
    rendered = json.loads((folder / video_id / "artifacts/video.mp4").read_text())
    assert rendered["input"] == str(folder / script_id / "artifacts/script.md")
    assert str(folder / notes_id / "artifacts/notes.md") in script.prompts[0]


def test_a_send_back_revises_the_previous_build_with_the_note_kept_as_a_version(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    def writes(name: str) -> Answer:  # the scripted CLI runs no tools, so the step writes
        return Answer(lambda options: (Path(options.cwd) / name).write_text("1"))

    liked, edit = writes("liked.css"), writes("ending.css")
    script = Script([liked, Say("v1")], [edit, Say("v2")])
    review = "  - gate: review\n    ask: Good?\n"
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{}", opened, transport, after=review
    )
    assert run["status"] == "waiting_gate", run
    video_id = run["stages"][1]["id"]
    stage = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"] / video_id
    first = (stage / "artifacts/video.mp4").read_text()
    assert "revision" not in script.prompts[0]

    note = "Slow the ending down; keep the opening."
    sent = client.post(
        f"/api/runs/{run['id']}/decision", json={"note": note, "send_back_to": "video"}
    )
    assert sent.status_code == 200, sent.text
    run = settled(client, run["id"], send_backs=1)
    assert run["status"] == "waiting_gate", run

    # the second session edits the kept build, told it revises and what to read first
    assert [o.cwd for o in script.sessions] == [stage / "build"] * 2
    prompt = script.prompts[1]
    assert prompt.startswith("This is a revision, not a fresh start.") and note in prompt
    assert f"{note} Change what the note asks and keep the rest as it is." in prompt
    assert f"before you change anything: {stage}/build.v1, {stage}/build.v1.mp4." in prompt
    assert (stage / "build/liked.css").is_file() and (stage / "build/ending.css").is_file()
    # the previous version stays beside it, inside the stage folder the session may read
    assert (stage / "build.v1/liked.css").is_file()
    assert not (stage / "build.v1/ending.css").exists()
    assert (stage / "build.v1.mp4").read_text() == first
    call = client.get(f"/api/stages/{video_id}/call").json()
    assert call["visit"] == 1 and call["request"]["site"] == str(stage / "build")


def test_a_failed_build_fails_the_stage_with_the_last_log_lines(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    script = Script([Call("Bash", {"command": "npx tsc --noEmit"}), Say("Gave up.")])

    class Failing(SdkRuntime):
        async def run(self, spec: Any, hooks: Any) -> Any:
            end = await super().run(spec, hooks)
            return type(end)(end.session_id, end.cost_usd, True, "tsc found 3 errors")

    client, run = build_run(tmp_path, Failing(transport=script.transport), "{}", opened, transport)
    assert run["status"] == "failed"
    error = run["stages"][1]["error"]
    assert "builder failed 2 sessions in a row" in error and "tsc found 3 errors" in error
    assert "tool: Bash" in error
    assert client.get(f"/api/stages/{run['stages'][1]['id']}/call").status_code == 404


def test_a_failed_render_fails_the_stage_with_its_error(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    transport.outputs["finish"] = [ConnectorError("the render exited with 1: no page")]
    script = Script([Say("Done.")])
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{}", opened, transport
    )
    assert run["status"] == "failed"
    assert "the render exited with 1: no page" in run["stages"][1]["error"]


def test_a_rerun_of_a_build_whose_render_failed_revises_it_in_place(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    """Issue 418: the render after the session failed, so the stage failed and the run waits at
    its Retro. A Rerun with a note revises the kept build in place and renders again."""

    def writes(name: str) -> Answer:
        return Answer(lambda options: (Path(options.cwd) / name).write_text("1"))

    skill = "---\nname: finish\ndescription: Test the finish.\n---\n\nRun it first.\n"
    proposal = {
        "skill": {
            "action": "new", "name": "finish", "content": skill, "summary": "Test the finish",
            "evidence": ["video: the render exited with 1"],
        },
        "workflow": None,
    }  # fmt: skip
    script = Script(
        [writes("v1.css"), Say("v1")],
        [Answer(proposal)],
        [writes("v2.css"), Say("v2")],
        [Answer({"skill": None, "workflow": None})],
    )
    transport.outputs["finish"] = [ConnectorError("the render exited with 1"), {"ok": True}]
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{}", opened, transport, retro=True
    )
    assert [s["status"] for s in run["stages"]] == ["done", "failed", "waiting"], run
    video_id = run["stages"][1]["id"]
    stage = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"] / video_id

    note = "Render it plainer."
    change = {"kind": "rerun", "stage": video_id, "payload": {"note": note}}
    proposed = client.post(f"/api/runs/{run['id']}/changes/propose", json=change).json()
    assert proposed["violations"] == [] and proposed["in_place"], proposed
    assert proposed["before"] == {"video": "failed", "retro": "waiting"}
    revision = proposed["revision"]
    applied = client.post(f"/api/runs/{run['id']}/changes", json={**change, "revision": revision})
    assert applied.status_code == 201, applied.text
    run = settled(client, run["id"], send_backs=1)
    assert run["status"] == "done", run

    revising = next(p for p in script.prompts if p.startswith("This is a revision"))
    assert note in revising and f"before you change anything: {stage}/build.v1." in revising
    assert (stage / "build.v1/v1.css").is_file() and not (stage / "build.v1/v2.css").exists()
    assert (stage / "build/v1.css").is_file() and (stage / "build/v2.css").is_file()
    call = client.get(f"/api/stages/{video_id}/call").json()
    assert (call["visit"], call["status"]) == (1, "done")


def until(client: TestClient, run_id: str, ready: Callable[[dict], bool]) -> dict:
    deadline = time.monotonic() + float(os.environ.get("SWARMIFY_BUILD_WAIT", 60))
    while not ready(run := client.get(f"/api/runs/{run_id}").json()):
        assert time.monotonic() < deadline, run
        time.sleep(0.05)
    return run


def _half(options: Any) -> None:
    """The builder's work before it is stopped: a file the starter has not."""
    (Path(options.cwd) / "half.css").write_text("/* half done */")


def _builder(client: TestClient, run_id: str, stage_id: str) -> dict:
    agents = client.get(f"/api/runs/{run_id}/agents").json()
    [agent] = [a for a in agents if a["stage_id"] == stage_id and a["name"] == "builder"]
    return agent


USAGE = {"input_tokens": 1000}
"""Each scripted step costs something, so the cost hook asks whether the run was paused."""


def test_a_build_paused_part_way_resumes_its_session_in_the_same_folder(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    """Issue 423: paused mid-build, the builder stops at its next step; on resume the stage runs
    again for the same visit, its folder is not seeded again and the builder continues its
    session, told to carry on rather than given the brief again."""
    started: list[str] = []

    def pause(options: Any) -> None:
        _half(options)
        [client] = opened
        assert client.post(f"/api/runs/{started[0]}/pause").status_code == 200

    script = Script(
        [Answer(pause), Call("Bash", {"command": "ls"}), Say("never")],
        [Say("Finished the second shot.")],
        usage=USAGE,
    )
    runtime = SdkRuntime(transport=script.transport)
    client, run = build_run(tmp_path, runtime, "{brief: Loud.}", opened, transport, wait=False)
    started.append(run["id"])
    run = until(client, run["id"], lambda r: r["status"] == "paused")
    video_id = run["stages"][1]["id"]
    stage = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"] / video_id
    assert (stage / "build/half.css").read_text() == "/* half done */"
    assert client.get(f"/api/stages/{video_id}/call").status_code == 404  # no render yet

    assert client.post(f"/api/runs/{run['id']}/resume").status_code == 200
    run = settled(client, run["id"])
    assert run["status"] == "done", run
    first, again = script.sessions
    assert first.resume is None and "Loud." in script.prompts[0]
    assert again.resume == _builder(client, run["id"], video_id)["session_id"]
    assert script.prompts[1] == build.CONTINUE
    assert (stage / "build/half.css").read_text() == "/* half done */", "not seeded again"


class Crash(BaseException):
    """The api process dying: DBOS records no outcome for the step, so the next start recovers
    the run and runs the stage again."""


def test_a_build_cut_short_by_a_restart_resumes_its_session_in_the_same_folder(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    """Issue 423: the api restarts mid-build (here the session's process dies with it, after its
    work); recovery runs the stage again for the same visit, which continues the session in the
    folder as it was."""
    script = Script([Answer(_half), Say("cut short")], [Say("Finished the second shot.")])
    sdk = SdkRuntime(transport=script.transport)

    class Restarts:
        async def run(self, spec: SessionSpec, hooks: SessionHooks) -> SessionEnd:
            end = await sdk.run(spec, hooks)
            if len(script.sessions) == 1:
                raise Crash()
            return end

    client, run = build_run(tmp_path, Restarts(), "{}", opened, transport, wait=False)
    until(client, run["id"], lambda _: len(script.sessions) == 1)
    time.sleep(0.5)  # the crash leaves the run in flight
    client.__exit__(None, None, None)
    opened.remove(client)

    app = create_app(
        tmp_path / "data", transport=transport, poll_seconds=0.05, retro=False, runtime=Restarts()
    )
    client = TestClient(app)
    client.__enter__()
    opened.append(client)
    run = settled(client, run["id"])
    assert run["status"] == "done", run
    video_id = run["stages"][1]["id"]
    stage = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"] / video_id
    first, again = script.sessions
    assert first.resume is None
    assert again.resume == _builder(client, run["id"], video_id)["session_id"]
    assert script.prompts[1] == build.CONTINUE
    assert (stage / "build/half.css").read_text() == "/* half done */", "not seeded again"


def test_a_build_session_that_died_is_continued_in_the_same_folder(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    script = Script([Answer(_half), Die()], [Say("Finished the second shot.")])
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{}", opened, transport
    )
    assert run["status"] == "done", run
    video_id = run["stages"][1]["id"]
    stage = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"] / video_id
    assert script.sessions[1].resume and script.prompts[1] == build.CONTINUE
    assert (stage / "build/half.css").read_text() == "/* half done */"


def test_a_pause_during_the_render_lands_after_it_and_resume_goes_on(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    """Issue 423: the render, a long job, is not stopped midway; the pause lands when it ends,
    the run log says so, and resume goes on from the next stage without building or rendering
    again."""
    transport.outputs["finish"] = [lambda a: {"job_id": a["output_path"], "poll": "finish_status"}]
    running: list[Any] = [{"status": "running", "progress": 0.5}] * 20
    transport.outputs["finish_status"] = [*running, {"status": "done"}]
    script = Script([Say("A slow render.")])
    after = "  - gate: review\n    ask: Good?\n"
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{}", opened, transport,
        after=after, wait=False,
    )  # fmt: skip
    run = until(client, run["id"], lambda r: r["stages"][1]["status"] == "running")
    video_id = run["stages"][1]["id"]
    deadline = time.monotonic() + 30
    while client.get(f"/api/stages/{video_id}/call").status_code != 200:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert client.post(f"/api/runs/{run['id']}/pause").status_code == 200
    run = until(client, run["id"], lambda r: r["status"] == "paused")
    assert [s["status"] for s in run["stages"][:2]] == ["done", "done"], run
    log = client.get(f"/api/runs/{run['id']}/stages/{video_id}/log").json()
    assert any("paused during the render" in e["detail"] for e in log)

    assert client.post(f"/api/runs/{run['id']}/resume").status_code == 200
    run = settled(client, run["id"])
    assert run["status"] == "waiting_gate", run
    assert len(script.sessions) == 1
    assert [c[1] for c in transport.calls if c[1] == "finish"] == ["finish"], "rendered once"


def test_a_revision_paused_part_way_keeps_its_edits_and_its_kept_version(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    """A send-back's visit paused mid-revision: on resume the half-done revision stays (it is not
    copied again from build.v1) and the builder continues the revision's session, not the first
    visit's."""
    started: list[str] = []

    def pause(options: Any) -> None:
        _half(options)
        [client] = opened
        assert client.post(f"/api/runs/{started[0]}/pause").status_code == 200

    script = Script(
        [Say("v1")],
        [Answer(pause), Call("Bash", {"command": "ls"}), Say("never")],
        [Say("v2")],
        usage=USAGE,
    )
    review = "  - gate: review\n    ask: Good?\n"
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{}", opened,
        transport=transport, after=review,
    )  # fmt: skip
    started.append(run["id"])
    video_id = run["stages"][1]["id"]
    first = _builder(client, run["id"], video_id)["session_id"]
    sent = client.post(
        f"/api/runs/{run['id']}/decision", json={"note": "More.", "send_back_to": "video"}
    )
    assert sent.status_code == 200, sent.text
    until(client, run["id"], lambda r: r["status"] == "paused")
    revising = _builder(client, run["id"], video_id)["session_id"]
    assert revising != first

    assert client.post(f"/api/runs/{run['id']}/resume").status_code == 200
    run = settled(client, run["id"], send_backs=1)
    assert run["status"] == "waiting_gate", run
    assert [o.resume for o in script.sessions] == [None, None, revising]
    assert (
        script.prompts[1].startswith("This is a revision") and script.prompts[2] == build.CONTINUE
    )
    stage = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"] / video_id
    assert (stage / "build/half.css").read_text() == "/* half done */"
    assert not (stage / "build.v1/half.css").exists() and (stage / "build.v1.mp4").is_file()


def test_a_preview_build_is_approved_at_a_gate_then_a_later_stage_renders_the_same_build(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    """A preview build, a gate that sends it back with notes or approves it, then a stage with
    `from` that renders that stage's build folder, with no session of its own."""

    def writes(name: str) -> Answer:  # the scripted CLI runs no tools, so the step writes
        return Answer(lambda options: (Path(options.cwd) / name).write_text("1"))

    script = Script([writes("v1.css"), Say("v1")], [writes("v2.css"), Say("v2")])
    after = (
        "  - gate: approve motion\n    ask: Approve the motion?\n"
        "  - connector: render\n    calls: sketch\n    output: video.mp4\n"
        "    params: {from: video}\n"
    )
    client, run = build_run(
        tmp_path,
        SdkRuntime(transport=script.transport),
        "{}",
        opened,
        inputs="input: script.md\n    output: preview.mp4",
        after=after,
        transport=transport,
    )
    assert run["status"] == "waiting_gate", run
    preview_id, render_id = run["stages"][1]["id"], run["stages"][3]["id"]
    folder = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"]
    assert (folder / preview_id / "artifacts/preview.mp4").is_file()

    sent = client.post(
        f"/api/runs/{run['id']}/decision", json={"note": "Slower.", "send_back_to": "video"}
    )
    assert sent.status_code == 200, sent.text
    run = settled(client, run["id"], send_backs=1)
    assert run["status"] == "waiting_gate", run
    assert "Slower." in script.prompts[1]

    assert client.post(f"/api/runs/{run['id']}/decision", json={}).status_code == 200
    run = until(client, run["id"], lambda r: r["status"] in ("done", "failed"))
    assert run["status"] == "done", run
    assert len(script.sessions) == 2  # the render stage ran no session
    project = folder / preview_id / "build"
    assert (project / "v2.css").is_file() and not (folder / render_id / "build").exists()
    call = client.get(f"/api/stages/{render_id}/call").json()
    assert (call["connector"], call["request"]["site"]) == ("sketch", str(project))
    assert (folder / render_id / "artifacts/video.mp4").is_file()


def test_a_build_from_a_stage_that_left_no_build_fails_saying_so(
    tmp_path: Path, transport: Rendering, opened: list[TestClient]
) -> None:
    after = (
        "  - gate: approve\n    ask: Go?\n"
        "  - connector: render\n    calls: sketch\n    output: video.mp4\n"
        "    params: {from: video}\n"
    )
    script = Script([Say("v1")])
    client, run = build_run(
        tmp_path, SdkRuntime(transport=script.transport), "{}", opened,
        transport, after=after,
    )  # fmt: skip
    folder = tmp_path / "data/worlds" / run["world_id"] / "runs" / run["id"]
    shutil.rmtree(folder / run["stages"][1]["id"] / "build")
    assert client.post(f"/api/runs/{run['id']}/decision", json={}).status_code == 200
    run = until(client, run["id"], lambda r: r["status"] in ("done", "failed"))
    assert run["status"] == "failed", run
    assert "video left no build" in json.dumps(run)
