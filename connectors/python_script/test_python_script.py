"""The python_script package: its tool against scripts in a temporary assets folder, then the
package installed through the real install path, enabled for a world and run by the core as a
connector stage, in the real connector sandbox: the script's stdout lands in the stage's output,
and a script trying to reach the network or write outside its stage folder is refused."""

import json
import shutil
from contextlib import closing
from pathlib import Path

import pytest
import python_script as ps
from mcp.server.mcpserver.exceptions import ToolError

from kriflow_connectors import (
    MIGRATIONS,
    Connectors,
    Credentials,
    McpTransport,
    SqliteStorage,
    read_manifest,
)
from kriflow_kernel import MIGRATIONS as KERNEL_MIGRATIONS
from kriflow_kernel.sqlite import connect, migrate

HERE = Path(__file__).parent
DOUBLE = "import json, sys\nd = json.load(sys.stdin)\nprint(json.dumps({'n': d['n'] * 2}))\n"
ESCAPE = """import json, socket, sys
out = {}
try:
    open(sys.argv[1], "w").write("x")
    out["wrote"] = True
except OSError:
    out["wrote"] = False
try:
    socket.create_connection(("203.0.113.7", 80), timeout=2)
    out["connected"] = True
except OSError:
    out["connected"] = False
print(json.dumps(out))
"""


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    folder = tmp_path / "assets"
    (folder / "tools").mkdir(parents=True)
    (folder / "tools" / "double.py").write_text(DOUBLE)
    (folder / "fails.py").write_text("import sys\nprint('half', file=sys.stderr)\nsys.exit(3)\n")
    (folder / "slow.py").write_text("import time\ntime.sleep(5)\n")
    (tmp_path / "in.json").write_text('{"n": 21}')
    return folder


def test_the_script_reads_the_input_as_stdin_and_its_stdout_is_the_output(
    assets: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.json"
    said = ps.run("tools/double.py", str(out), str(assets), str(tmp_path / "in.json"))
    assert json.loads(out.read_text()) == {"n": 42}
    assert said == {"output": str(out), "bytes": out.stat().st_size, "script": "tools/double.py"}
    # with only `inputs`, the first is stdin
    ps.run("tools/double.py", str(out), str(assets), input_paths=[str(tmp_path / "in.json")])
    assert json.loads(out.read_text()) == {"n": 42}


@pytest.mark.parametrize("script", ["../in.json", "/etc/hosts", "nope.py", "tools"])
def test_only_a_py_file_in_the_assets_runs(assets: Path, tmp_path: Path, script: str) -> None:
    with pytest.raises(ToolError, match="is not a .py file in the world's assets"):
        ps.run(script, str(tmp_path / "out.json"), str(assets))


def test_a_failing_or_slow_script_fails_the_stage_and_writes_nothing(
    assets: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.json"
    with pytest.raises(ToolError, match="fails.py exited 3:\nhalf"):
        ps.run("fails.py", str(out), str(assets))
    with pytest.raises(ToolError, match="ran past its 1 s limit"):
        ps.run("slow.py", str(out), str(assets), seconds=1)
    assert not out.exists()


def test_the_manifest_asks_for_no_network_and_reads_only_the_worlds_assets() -> None:
    m = read_manifest(HERE)
    assert (m["name"], m["kind"], m["enable_by_default"]) == ("python_script", "stage", False)
    assert m["permissions"] == {"folders": ["worlds/{world}/assets"]}
    assert m["params"]["required"] == ["script"] and "capabilities" not in m


def test_the_installed_package_runs_a_stage_in_the_sandbox(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(
        HERE,
        data / "repo" / "connectors" / "python_script",
        ignore=shutil.ignore_patterns(".venv", "__pycache__"),
    )
    db = data / "swarmify.db"
    with closing(connect(db)) as conn:
        migrate(conn, KERNEL_MIGRATIONS, MIGRATIONS)
    connectors = Connectors(SqliteStorage(db), McpTransport(), Credentials(data / ".env"), 0)
    connectors.packages.repo = data / "repo"
    version = read_manifest(HERE)["version"]
    assert connectors.install_defaults() == {}  # installed, but not a default
    connectors.packages.manifest("python_script", version)
    connectors.enable("w", "python_script", version)
    world = data / "worlds" / "w"
    (world / "assets").mkdir(parents=True)
    (world / "assets" / "double.py").write_text(DOUBLE)
    (world / "assets" / "escape.py").write_text(ESCAPE)
    given = world / "runs" / "r" / "s0" / "artifacts" / "in.json"
    given.parent.mkdir(parents=True)
    given.write_text('{"n": 4}')

    def stage(stage_id: str, script: str, *args: str) -> Path:
        out = world / "runs" / "r" / stage_id / "artifacts" / "out.json"
        out.parent.mkdir(parents=True)
        params = {
            "script": script,
            "input_path": str(given),
            "input_paths": list(args),
            "output_path": str(out),
            "assets_path": str(world / "assets"),
        }
        config = {"calls": "python_script", "params": params}
        call = connectors.run_stage("w", "r", stage_id, config)
        assert call.status == "done", call
        return out

    assert json.loads(stage("s1", "double.py").read_text()) == {"n": 8}
    outside = world / "assets" / "written.txt"
    said = json.loads(stage("s2", "escape.py", str(outside)).read_text())
    assert said == {"wrote": False, "connected": False}
    assert not outside.exists()
