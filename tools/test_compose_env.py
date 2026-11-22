"""compose.yaml checks required variables per service, so one service can start alone."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
NEEDS = ROOT / "docker" / "needs.sh"


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(NEEDS), *args],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
    )


def test_needs_refuses_while_a_variable_is_empty_and_names_it() -> None:
    out = run({"A": "set"}, "A", "B", "--", "echo", "started")
    assert out.returncode == 1 and "B" in out.stderr and "started" not in out.stdout


def test_needs_starts_the_command_once_every_variable_is_set() -> None:
    out = run({"A": "1", "B": "2"}, "A", "B", "--", "echo", "started")
    assert out.returncode == 0 and out.stdout.strip() == "started"


def test_compose_has_no_file_wide_required_variables() -> None:
    # A `${VAR:?}` anywhere makes every `docker compose` command fail without VAR, Kokoro included.
    assert ":?" not in (ROOT / "compose.yaml").read_text()


def test_only_web_is_published_and_the_api_image_has_no_skin() -> None:
    # A UI-only change must rebuild and restart only `web`, never the api and its runs (#181).
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    assert services["web"]["build"]["target"] == "web" and services["web"]["ports"]
    assert "ports" not in services["api"] and "depends_on" not in services["web"]
    api_stage = (ROOT / "Dockerfile").read_text().rsplit("\nFROM ", 1)[1]
    assert "apps/web" not in api_stage and "--from=web" not in api_stage
    caddy = (ROOT / "docker" / "Caddyfile").read_text()
    assert "flush_interval -1" in caddy and "handle /mcp" in caddy
    assert "forward_auth" not in caddy, "the skin loads unsigned so its sign-in page can show"


def test_claude_transcripts_live_on_the_volume_in_the_api_and_the_sandbox() -> None:
    # A session resumes after `docker compose up -d --build` only if its transcript survived: the
    # api's agents (#387) and the Build Sessions whose CLI runs in `sandbox` (#423), each on its
    # own subpath, which `folders` makes before either starts.
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    subpaths = {}
    for name in ("api", "sandbox"):
        [mount] = [
            v
            for v in compose[name]["volumes"]
            if isinstance(v, dict) and v["target"] == "/root/.claude"
        ]
        assert mount["source"] == "data"
        subpaths[name] = mount["volume"]["subpath"]
    assert subpaths == {"api": "claude", "sandbox": "claude-builds"}
    made = compose["folders"]["entrypoint"]
    assert all(f"/data/{s}" in made for s in subpaths.values())


@pytest.mark.skipif(not shutil.which("docker"), reason="needs the docker compose CLI")
def test_an_env_of_old_names_fills_the_new_ones_on_the_same_project(tmp_path: Path) -> None:
    """The box's .env says SWARMIFY_* (kriflow#594): the api gets KRIFLOW_* from it, the old
    names stay beside them for the startup log, and the project, volumes and network are the
    same."""
    old = {
        "SWARMIFY_PASSWORD": "aviato",
        "SWARMIFY_BIND": "127.0.0.1",
        "SWARMIFY_RESOURCES": "gpu=1",
        "SWARMIFY_VAPID_PUBLIC_KEY": "pub",
        "SWARMIFY_SANDBOX_APPARMOR": "swarmify-sandbox",
    }
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in old.items()))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("COMPOSE_", "KRIFLOW_", "SWARMIFY_"))
    }
    out = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "--project-directory",
         str(tmp_path), "config", "--format", "json"],
        capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip
    rendered = json.loads(out.stdout)
    assert rendered["name"] == "swarmify"
    assert {v["name"] for v in rendered["volumes"].values()} == {
        "swarmify_data",
        "swarmify_sandbox-socket",
    }
    assert rendered["networks"]["default"]["name"] == "swarmify"
    api = rendered["services"]["api"]["environment"]
    assert api["KRIFLOW_PASSWORD"] == api["SWARMIFY_PASSWORD"] == "aviato"
    assert api["KRIFLOW_RESOURCES"] == "gpu=1" and api["KRIFLOW_VAPID_PUBLIC_KEY"] == "pub"
    assert api["KRIFLOW_DRAIN_SECONDS"] == "1200" and api["SWARMIFY_DRAIN_SECONDS"] == ""
    assert api["KRIFLOW_DATA_DIR"] == "/data"
    [port] = rendered["services"]["web"]["ports"]
    assert port["host_ip"] == "127.0.0.1"
    assert "apparmor=swarmify-sandbox" in rendered["services"]["sandbox"]["security_opt"]
