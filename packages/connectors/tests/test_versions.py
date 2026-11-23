"""Versions and upgrades (issue 223): two versions of one package side by side; a run resolves
the version its workflow version recorded, a switch shows the Manifest diff and waits for a new
required Setup field, and an installed version can be removed."""

import shutil
from pathlib import Path

import pytest
from test_enable import FIXTURES

from kriflow_connectors import Connectors, FakeTransport, InstallError, Packages
from kriflow_connectors.packages import manifest_diff


@pytest.fixture
def packages(connectors: Connectors) -> Packages:
    installed = Packages(connectors.packages.root, FIXTURES)
    installed.install("packages/echo")
    installed.install("packages/echo_2")
    return installed


def test_a_run_resolves_the_recorded_version_not_the_worlds_pin(
    connectors: Connectors, packages: Packages, transport: FakeTransport
) -> None:
    connectors.enable("w1", "echo", "1.0.0")
    connectors.save_setup("w1", "echo", {"api_key": "k"}, {"region": "eu"}, "2.0.0")
    connectors.enable("w1", "echo", "2.0.0")
    transport.versions.clear()  # what the Setup test tool saw
    stage = {"calls": "echo", "params": {"tool": "echo"}}
    connectors.run_stage("w1", "r1", "s1", stage, pins={"echo": "1.0.0"})
    connectors.run_stage("w1", "r2", "s2", stage)
    assert transport.versions == ["1.0.0", "2.0.0"]
    pinned = connectors.find("w1", "echo", {"echo": "1.0.0"})
    assert pinned is not None and pinned.folder == str(packages.root / "echo/1.0.0")
    assert connectors.agent_servers("w1", ["echo"], {"echo": "1.0.0"})["echo"]["type"] == "stdio"

    connectors.disable("w1", "echo")  # a recorded version never brings back a disabled package
    assert connectors.find("w1", "echo", {"echo": "1.0.0"}) is None


def test_a_switch_shows_the_manifest_diff_and_waits_for_a_new_required_field(
    connectors: Connectors, packages: Packages
) -> None:
    first = connectors.switch("w1", "echo", "1.0.0")
    assert (first["from"], first["needs"]) == (None, [])  # a first enable never waits
    connectors.enable("w1", "echo", "1.0.0")

    diff = connectors.switch("w1", "echo", "2.0.0")
    assert (diff["from"], diff["to"]) == ("1.0.0", "2.0.0")
    assert diff["params"] == {"added": ["speed"], "removed": [], "changed": []}
    assert diff["setup"] == {"added": ["region"], "removed": [], "changed": []}
    assert diff["required"] == ["region"] and diff["needs"] == ["Region"]
    assert diff["permissions"] == {"added": ["network: eu.example.org"], "removed": []}
    with pytest.raises(ValueError, match="echo 2.0.0 needs a value for Region first"):
        connectors.enable("w1", "echo", "2.0.0")
    assert connectors.enabled("w1") == {"echo": "1.0.0"}

    with pytest.raises(ValueError, match="Region: is required"):  # its form is 2.0.0's
        connectors.save_setup("w1", "echo", {"api_key": "k"}, {}, "2.0.0")
    connectors.save_setup("w1", "echo", {"api_key": "k"}, {"region": "eu"}, "2.0.0")
    assert connectors.switch("w1", "echo", "2.0.0")["needs"] == []
    connectors.enable("w1", "echo", "2.0.0")
    back = connectors.switch("w1", "echo", "1.0.0")  # going back drops the field, never waits
    assert back["setup"]["removed"] == ["region"] and back["needs"] == []
    assert (
        connectors.pinned_by("echo", "2.0.0") == ["w1"]
        and connectors.pinned_by("echo", "1.0.0") == []
    )


def test_a_sign_in_that_becomes_per_world_is_copied_into_the_world_moving_its_pin(
    connectors: Connectors, tmp_path: Path
) -> None:
    """A field shared in the pinned version and set per world in the next starts as a copy of
    the shared value in the world that moves (issue 610). The shared value stays for other
    worlds and for the older version a saved workflow version recorded."""
    manifest = (FIXTURES / "packages/echo/swarmify-connector.toml").read_text()
    oauth = 'key = "account"\nlabel = "Account"\ntype = "oauth"\nscope = "world"'
    assert oauth in manifest
    for folder, version, scope in (("v1", "1.0.0", "shared"), ("v2", "1.1.0", "world")):
        shutil.copytree(FIXTURES / "packages/echo", tmp_path / "src" / folder)
        (tmp_path / "src" / folder / "swarmify-connector.toml").write_text(
            manifest.replace('version = "1.0.0"', f'version = "{version}"', 1).replace(
                oauth, oauth.replace('"world"', f'"{scope}"')
            )
        )
        Packages(connectors.packages.root, tmp_path / "src").install(folder)
    tokens = {"access_token": "a", "refresh_token": "r", "expires_at": 9e9}
    for world in ("w1", "w2"):
        connectors.enable(world, "echo", "1.0.0")
    connectors.setups.put_value("echo", None, "account", tokens)

    connectors.enable("w1", "echo", "1.1.0")
    assert connectors.setups.values("echo", "w1") == ({"account": tokens}, {"account": tokens})
    moved = connectors.find("w1", "echo")
    assert moved is not None and connectors.sign_in_needed(moved) == []
    assert connectors.setups.values("echo", "w2")[1] == {}  # w2 never moved: nothing copied
    connectors.enable("w2", "echo", "1.0.0")  # a move to a shared version copies nothing
    assert connectors.setups.values("echo", "w2")[1] == {}

    mine = {**tokens, "access_token": "b"}  # a world's own sign-in is never overwritten
    connectors.setups.put_value("echo", "w2", "account", mine)
    connectors.enable("w2", "echo", "1.1.0")
    assert connectors.setups.values("echo", "w2")[1] == {"account": mine}


def test_uninstall_removes_one_version_and_keeps_the_other(packages: Packages) -> None:
    packages.uninstall("echo", "1.0.0")
    assert [(p.name, p.version) for p in packages.list()] == [("echo", "2.0.0")]
    with pytest.raises(InstallError, match="echo 1.0.0 is not installed"):
        packages.uninstall("echo", "1.0.0")
    packages.uninstall("echo", "2.0.0")
    assert packages.list() == [] and not (packages.root / "echo").exists()


def test_a_renamed_or_defaulted_field_is_not_newly_required() -> None:
    old = {"config": [{"key": "token", "label": "Token", "type": "string", "required": True}]}
    new = {
        "config": [
            {
                "key": "api_token",
                "label": "Token",
                "type": "string",
                "required": True,
                "renamed_from": "token",
            },
            {
                "key": "region",
                "label": "Region",
                "type": "string",
                "required": True,
                "default": "eu",
            },
            {"key": "zone", "label": "Zone", "type": "string", "required": True},
        ]
    }
    assert manifest_diff(old, new)["required"] == ["zone"]
