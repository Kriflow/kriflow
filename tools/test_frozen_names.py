"""Names kept through the rename to Kriflow (kriflow#564): each is stored on disk or in a database,
joins two compose projects, or would sign every device out. A search and replace that changes
one fails here, in CI, instead of on the box. Each is asserted where the code defines it."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from swarmify_api.auth import COOKIE, Operator
from swarmify_api.main import migrated_db
from swarmify_connectors.packages import RECORD
from swarmify_runs import runner
from swarmify_runs.control import APP_NAME, SCHEDULED_WORKFLOW, WAKE
from swarmify_swarm.ports import TOOL_SERVER, tool_id
from swarmify_swarm.sqlite import SYSTEM
from tools import backup_data, gpu_compose

ROOT = Path(__file__).resolve().parents[1]
DBOS_NAMES = {
    "swarmify.run",
    "swarmify.stage",
    "swarmify.scheduled",
    "swarmify.start_scheduled",
    "swarmify.ready",
    "swarmify.wake",
    "swarmify.left",
    "swarmify.world_of",
    "swarmify.load",
    "swarmify.start_child",
    "swarmify.fan_next",
    "swarmify.take",
    "swarmify.drop_change",
    "swarmify.save",
    "swarmify.execute",
}


def test_compose_pins_the_project_its_volumes_and_its_network() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert compose["name"] == "swarmify"  # the volumes below are swarmify_data and so on
    assert set(compose["volumes"]) == {"data", "sandbox-socket"}
    assert compose["networks"]["default"]["name"] == "swarmify"
    # the speech package's compose file joins this network: Kriflow/packages tests its names
    # compose.gpu.yaml merges over compose.yaml and must not name a project, volume or network
    assert set(gpu_compose.compose({"devices": []})) == {"services"}


@pytest.mark.skipif(not shutil.which("docker"), reason="needs the docker compose CLI")
def test_compose_renders_the_same_names_from_a_folder_named_kriflow(tmp_path: Path) -> None:
    folder = tmp_path / "kriflow"
    folder.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    out = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "--project-directory",
         str(folder), "config", "--format", "json"],
        capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip
    rendered = json.loads(out.stdout)
    assert rendered["name"] == "swarmify"
    assert {v["name"] for v in rendered["volumes"].values()} == {
        "swarmify_data",
        "swarmify_sandbox-socket",
    }
    assert rendered["networks"]["default"]["name"] == "swarmify"


def test_stored_files_keep_their_names(tmp_path: Path) -> None:
    assert migrated_db(tmp_path) == tmp_path / "swarmify.db"
    # the MCP process and the graph writer open the same file by its own literal
    literals = {
        m
        for src in [*ROOT.glob("packages/*/src"), ROOT / "apps/api/src"]
        for f in src.rglob("*.py")
        for m in re.findall(r'"([\w.-]+\.db)"', f.read_text())
    }
    assert literals == {"swarmify.db"}
    assert RECORD == ".swarmify-install.json"
    assert backup_data.PREFIX == "swarmify-data-backup-"


def test_dbos_names_stay_as_recorded() -> None:
    assert APP_NAME == "swarmify"
    assert runner.APP_VERSION == "6"  # bumped on purpose only, when the step order changes
    assert WAKE == {"swarmify": "wake"}
    assert SCHEDULED_WORKFLOW == "swarmify.scheduled"
    registered = {
        f.dbos_function_name for f in vars(runner).values() if hasattr(f, "dbos_function_name")
    }
    assert registered == DBOS_NAMES


def test_sign_in_and_tokens_keep_their_names(tmp_path: Path) -> None:
    assert COOKIE == "swarmify_session"
    assert Operator(migrated_db(tmp_path)).create_token("ci").token.startswith("swm_")


def test_agents_and_the_whiteboard_keep_their_names() -> None:
    assert SYSTEM == "swarmify"
    assert TOOL_SERVER == "swarmify" and tool_id("x") == "mcp__swarmify__x"
