"""tools/deploy.py against a fake box: what it runs, in which order, and when it stops."""

import json
from pathlib import Path

from tools import deploy

BUSY = {"run_name": "Weekly", "stage": "render", "type": "connector"}


def state(name: str, left: float | None = 600.0, *busy: dict) -> str:
    return json.dumps({"state": name, "seconds_left": left, "at_work": list(busy)})


class Box:
    """Records every command. `swarmify-drain status` and `start` answer from `states` in order
    (the last repeats), after the serving api the first status finds; a command containing a
    word in `fails` exits 1."""

    def __init__(self, *states: str, fails: tuple[str, ...] = ()) -> None:
        self.cmds: list[list[str]] = []
        self.states = [state("serving"), *states]
        self.fails = fails
        self.said: list[str] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.cmds.append(cmd)
        if any(word in cmd for word in self.fails):
            return 1, ""
        if "swarmify-drain" not in cmd:
            return 0, ""
        action = cmd[cmd.index("swarmify-drain") + 1]
        if action == "cancel":
            return 0, state("serving")
        return 0, self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def steps(self) -> list[str]:
        """Each command as its first telling words."""
        out = []
        for cmd in self.cmds:
            if "swarmify-drain" in cmd:
                out.append("drain " + cmd[cmd.index("swarmify-drain") + 1])
            elif cmd[:2] == ["docker", "compose"]:
                out.append(next(w for w in cmd[2:] if not w.startswith("-") and "yaml" not in w))
            else:
                out.append("backup")
        return out

    def deploy(self, *argv: str, root: Path = Path("/nowhere")) -> int:
        return deploy.deploy(list(argv), self, lambda _s: None, self.said.append, root)


def test_it_builds_while_serving_drains_stops_the_api_first_and_waits_for_the_new_one() -> None:
    box = Box(
        state("draining", 500.0, BUSY),  # the start
        state("draining", 490.0, BUSY),
        state("drained", 480.0),
        state("serving"),  # the new api
    )
    assert box.deploy() == 0
    assert box.steps() == [
        "drain status",
        "build",
        "drain start",
        "drain status",
        "drain status",
        "stop",
        "up",
        "drain status",
    ]
    assert box.cmds[1][-4:] == ["api", "web", "sandbox", "graph-writer"]
    assert box.cmds[5][-3:] == ["-t", "60", "api"]
    assert "--no-build" in box.cmds[6]
    assert "-e" in box.cmds[0] and "SWARMIFY_API_TOKEN" in box.cmds[0]
    assert box.said[-1] == "Deployed. Waited for: Weekly: render (connector)."


def test_a_web_only_deploy_recreates_web_alone_without_a_drain() -> None:
    box = Box()
    assert box.deploy("--web-only") == 0
    assert box.cmds == [
        ["docker", "compose", "-f", "compose.yaml", "up", "-d", "--build", "--no-deps", "web"]
    ]


def test_a_failed_build_never_starts_a_drain() -> None:
    box = Box(fails=("build",))
    assert box.deploy() == 1
    assert box.steps() == ["drain status", "build"]
    assert "the api still serves" in box.said[-1]


def test_a_refused_token_stops_before_anything_changes() -> None:
    box = Box(fails=("swarmify-drain",))
    assert box.deploy() == 1
    assert box.steps() == ["drain status"]
    assert "SWARMIFY_API_TOKEN" in box.said[-1]


def test_at_the_limit_it_cancels_the_drain_and_stops_by_default() -> None:
    box = Box(state("draining", 10.0, BUSY), state("draining", 0.0, BUSY))
    assert box.deploy("--seconds", "10") == 3
    assert box.steps()[-1] == "drain cancel" and "stop" not in box.steps()
    assert box.cmds[2][-2:] == ["--seconds", "10.0"]
    assert "  Weekly: render (connector)" in box.said


def test_with_cut_it_goes_on_at_the_limit_and_says_what_it_cut() -> None:
    box = Box(state("draining", 0.0, BUSY), state("serving"))
    assert box.deploy("--cut") == 0
    assert box.steps()[-3:] == ["stop", "up", "drain status"]
    assert box.said[-1] == "Cut, running again now: Weekly: render (connector)."


def test_the_backup_comes_first_and_a_failed_one_deploys_nothing(tmp_path: Path) -> None:
    (tmp_path / "compose.gpu.yaml").write_text("services: {}\n")
    box = Box(state("drained"), state("serving"))
    assert box.deploy("--backup", "pre-571", root=tmp_path) == 0
    assert box.steps()[0] == "backup"
    assert box.cmds[0][1:] == ["tools/backup_data.py", "pre-571", "--dest", "/data", "--keep", "3"]
    assert box.cmds[1][:6] == ["docker", "compose", "-f", "compose.yaml", "-f", "compose.gpu.yaml"]

    failed = Box(fails=("tools/backup_data.py",))
    assert failed.deploy("--backup", "x") == 1
    assert failed.steps() == ["backup"]
