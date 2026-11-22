"""Resources (ADR 0030): a package's declared Resources reach its processes as folders of Slot
files, a process holds a Slot by flock (so any exit frees it), and every package process knows
its run. The Resource here is `seat`, a one-seat service: nothing about it is a GPU."""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from swarmify_connectors import Connectors, Credentials, McpTransport, SqliteStorage, resources
from swarmify_connectors.packages import MANIFEST
from swarmify_connectors.sandbox import Grants, Sandbox

PROBE = Path(__file__).parent / "fixtures/packages/probe"
NOTE = {"run": "run_1", "root": "run_1", "stage": "s1", "package": "probe"}


def install(connectors: Connectors, tmp_path: Path, name: str, permissions: str = "") -> None:
    """A copy of the probe package (it answers with its environment) as `name`, started with
    this Python, with `permissions` as its `[permissions]` table."""
    source = tmp_path / "src" / name
    shutil.copytree(PROBE, source)
    toml = (source / MANIFEST).read_text().replace('name = "probe"', f'name = "{name}"')
    toml = toml.replace('["python3", "server.py"]', json.dumps([sys.executable, "server.py"]))
    if permissions:
        toml += f"\n[permissions]\n{permissions}\n"
    (source / MANIFEST).write_text(toml)
    connectors.packages.install(str(source))
    connectors.enable("w1", name, "1.0.0")
    connectors.setups.put(name, "w1", {"api_key": "k"}, {})


@pytest.fixture
def real(tmp_path: Path, db: Path) -> Connectors:
    return Connectors(SqliteStorage(db), McpTransport(), Credentials(tmp_path / ".env"), 0.05)


def test_a_declared_resource_and_the_run_identity_reach_the_process_and_nothing_else_does(
    real: Connectors, tmp_path: Path
) -> None:
    install(real, tmp_path, "seated", 'resources = ["seat"]')
    install(real, tmp_path, "plain")
    real.root_of = lambda run_id: "run_top"

    def environ(name: str) -> dict[str, str]:
        call = real.run_stage(
            "w1", "run_child", f"s_{name}", {"calls": name, "params": {"tool": "environ"}}
        )
        assert (call.response or {})["wrote"]  # its stage's folder, which it may write
        return (call.response or {})["env"]

    seated, plain = environ("seated"), environ("plain")
    folder = tmp_path.resolve() / "sandbox" / "resources" / "seat"
    assert seated["SWARMIFY_RESOURCE_SEAT"] == str(folder)
    assert seated["SWARMIFY_RESOURCE_SEAT_SLOTS"] == "1"
    assert not [k for k in plain if k.startswith("SWARMIFY_RESOURCE") or k == "SWARMIFY_GPU_LOCK"]
    for env, stage in ((seated, "s_seated"), (plain, "s_plain")):
        assert env["SWARMIFY_RUN_ID"] == "run_child"
        assert env["SWARMIFY_RUN_ROOT"] == "run_top"
        assert env["SWARMIFY_STAGE_ID"] == stage
        folder = Path(env["SWARMIFY_STAGE_DIR"])
        assert folder == real.data_dir / "worlds/w1/runs/run_child" / stage
        assert (folder / "probe.txt").read_text() == "x"


TAKE = """
import fcntl, os
folder = os.environ["SWARMIFY_RESOURCE_{name}"]
(open(os.path.join(folder, "mine.txt"), "w")).write("x")
with open(os.path.join(folder, "0.lock"), "a") as slot:
    fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
print(os.environ["SWARMIFY_RESOURCE_{name}_SLOTS"], os.environ.get("SWARMIFY_GPU_LOCK", "none"))
"""


@pytest.mark.parametrize(
    ("grants", "name", "lock"),
    [(Grants(resources=["seat"]), "SEAT", False), (Grants(gpu=True), "GPU", True)],
)
def test_a_process_writes_its_resource_folder_inside_the_sandbox(
    tmp_path: Path, grants: Grants, name: str, lock: bool
) -> None:
    """`gpu = true` alone is the gpu Resource, and `SWARMIFY_GPU_LOCK` its first Slot."""
    sandbox = Sandbox(tmp_path)
    confined = sandbox.wrap([sys.executable, "-c", TAKE.replace("{name}", name)], None, grants)
    done = subprocess.run(confined.argv, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    folder = sandbox.resources / name.lower()
    assert (folder / "mine.txt").read_text() == "x"
    gpu_lock = str(folder / "0.lock") if lock else "none"
    assert done.stdout.split() == ["1", gpu_lock]


def _overlap(tmp_path: Path) -> list[tuple[str, float, float]]:
    """Two holders of `seat`, each for 0.6 s: their (name, start, end)."""
    place, slots = resources.folder(tmp_path, "seat")
    spans: list[tuple[str, float, float]] = []

    def hold(name: str) -> None:
        with resources.hold(place, slots, {**NOTE, "run": name}):
            start = time.monotonic()
            time.sleep(0.6)
            spans.append((name, start, time.monotonic()))

    threads = [threading.Thread(target=hold, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(spans, key=lambda s: s[1])


def test_two_holders_of_a_one_slot_resource_take_turns(tmp_path: Path) -> None:
    first, second = _overlap(tmp_path)
    assert second[1] >= first[2]


def test_two_slots_let_two_holders_in_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMIFY_RESOURCES", "gpu=1, seat=2")
    first, second = _overlap(tmp_path)
    assert second[1] < first[2]


HOLDER = """
import sys, time
from pathlib import Path
from swarmify_connectors import resources
place, slots = resources.folder(Path(sys.argv[1]), "seat")
with resources.hold(place, slots, {"run": "run_dead", "stage": "s1"}):
    print("held", flush=True)
    time.sleep(60)
"""


def test_a_killed_holder_frees_its_slot_and_its_note_goes_stale(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    assert holder.stdout and holder.stdout.readline().strip() == "held"
    place, slots = resources.folder(tmp_path, "seat")
    [seat] = resources.snapshot(tmp_path)
    assert seat["slots"][0]["holder"]["run"] == "run_dead"

    waited: list[bool] = []
    took = threading.Event()

    def wait() -> None:
        with resources.hold(place, slots, NOTE, lambda: waited.append(True)):
            took.set()
            time.sleep(0.3)

    waiter = threading.Thread(target=wait)
    waiter.start()
    deadline = time.monotonic() + 5
    while not resources.snapshot(tmp_path)[0]["waiting"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    [seat] = resources.snapshot(tmp_path)
    assert [w["run"] for w in seat["waiting"]] == ["run_1"] and waited == [True]
    os.kill(holder.pid, signal.SIGKILL)
    holder.wait()
    assert took.wait(5)
    waiter.join()

    (place / "0.holder").write_text(json.dumps({"run": "run_dead"}))  # as a kill leaves it
    [seat] = resources.snapshot(tmp_path)
    assert seat["slots"] == [{"slot": 0, "held": False, "holder": None}]
    assert seat["waiting"] == []


def test_a_tool_server_lent_to_a_stage_gets_its_resources_and_its_run(
    real: Connectors, tmp_path: Path
) -> None:
    install(real, tmp_path, "seated", 'resources = ["seat"]')
    real.root_of = lambda run_id: "run_top"
    [config] = real.agent_servers("w1", ["seated"], run=("run_1", "s_swarm")).values()
    plan = json.loads((Path(config["stderr"]).parent / "plan.json").read_text())
    env = plan["env"]
    assert env["SWARMIFY_RESOURCE_SEAT"].endswith("/sandbox/resources/seat")
    assert env["SWARMIFY_RESOURCE_SEAT_SLOTS"] == "1"
    assert (env["SWARMIFY_RUN_ID"], env["SWARMIFY_RUN_ROOT"]) == ("run_1", "run_top")
    assert env["SWARMIFY_STAGE_ID"] == "s_swarm"
    # the path only: a swarm stage's tool server without its login is not confined to the stage,
    # and neither reads nor writes its folder
    assert env["SWARMIFY_STAGE_DIR"] == str(real.data_dir / "worlds/w1/runs/run_1/s_swarm")
    folder = Path(env["SWARMIFY_STAGE_DIR"]).resolve()
    writes = [Path(p).resolve() for p in plan["sandbox"]["filesystem"]["allowWrite"]]
    assert writes and not any(folder.is_relative_to(w) for w in writes)
