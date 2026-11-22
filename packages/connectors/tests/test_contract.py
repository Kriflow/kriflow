"""The Package Contract (issue 582): a Manifest's `contract` is checked against the range this
core runs before anything else of it, at review, install, the installed list, enable and a
stage; the curated index says which entries this core can run."""

import shutil
from pathlib import Path

import pytest

import swarmify_connectors.packages as module
from swarmify_connectors import (
    ConnectorError,
    Connectors,
    InstallError,
    ManifestError,
    Packages,
    read_manifest,
)
from swarmify_connectors.index import read_index
from swarmify_connectors.packages import MANIFEST

FIXTURES = Path(__file__).parent / "fixtures"
NEWER = (
    "echo 1.0.0 needs package contract 3, and this Kriflow runs contracts 1 to 2."
    " Update Kriflow first, or install an older echo."
)


def echo(tmp_path: Path, head: str) -> Path:
    """The echo fixture with `head` above its Manifest; its install step marks that it ran."""
    folder = tmp_path / "src/echo"
    shutil.copytree(FIXTURES / "packages/echo", folder)
    (folder / MANIFEST).write_text(f"{head}\n{(folder / MANIFEST).read_text()}")
    return folder


def test_a_manifest_without_a_contract_reads_as_1_and_one_inside_the_range_passes(
    tmp_path: Path,
) -> None:
    assert "contract" not in read_manifest(FIXTURES / "packages/echo")
    assert read_manifest(echo(tmp_path, "contract = 1"))["contract"] == 1


def test_a_newer_contract_is_refused_for_its_contract_before_its_unknown_field(
    tmp_path: Path,
) -> None:
    folder = echo(tmp_path, 'contract = 3\nfield_of_contract_3 = "yes"')
    with pytest.raises(ManifestError) as refused:
        read_manifest(folder)
    assert str(refused.value) == NEWER


def test_the_messages_name_the_range_and_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "OLDEST_CONTRACT", 2)
    monkeypatch.setattr(module, "NEWEST_CONTRACT", 3)
    with pytest.raises(ManifestError) as older:
        read_manifest(FIXTURES / "packages/echo")  # no contract: 1
    assert str(older.value) == (
        "echo 1.0.0 was written for package contract 1, and this Kriflow runs contracts 2 to 3."
        " Install a newer echo."
    )
    with pytest.raises(ManifestError) as newer:
        read_manifest(echo(tmp_path, "contract = 4"))
    assert str(newer.value).startswith("echo 1.0.0 needs package contract 4, and this Kriflow")
    assert "runs contracts 2 to 3. Update Kriflow first, or install an older echo." in str(
        newer.value
    )


@pytest.mark.parametrize("value", ["0", '"2"', "1.5", "true"])
def test_a_contract_that_is_not_a_whole_number_from_1_is_a_schema_error(
    tmp_path: Path, value: str
) -> None:
    with pytest.raises(ManifestError, match="^contract: "):
        read_manifest(echo(tmp_path, f"contract = {value}"))


def test_review_and_install_refuse_a_newer_contract_before_any_step_runs(tmp_path: Path) -> None:
    packages = Packages(tmp_path / "data/packages", FIXTURES)
    folder = echo(tmp_path, "contract = 3")
    with pytest.raises(ManifestError, match="needs package contract 3"):
        packages.review(str(folder))
    with pytest.raises(ManifestError, match="needs package contract 3"):
        packages.install(str(folder))
    assert not (folder / "installed.txt").exists() and not packages.root.exists()


def test_an_installed_version_outside_the_range_lists_invalid_and_does_not_enable(
    connectors: Connectors,
) -> None:
    packages = Packages(connectors.packages.root, FIXTURES)
    packages.install("packages/echo")
    connectors.enable("w1", "echo", "1.0.0")
    toml = packages.root / "echo/1.0.0" / MANIFEST
    toml.write_text(f"contract = 3\n{toml.read_text()}")
    [listed] = packages.list()
    assert (listed.state, listed.problem) == ("invalid", NEWER)
    with pytest.raises(ValueError, match="^echo 1.0.0 needs package contract 3"):
        connectors.enable("w2", "echo", "1.0.0")
    with pytest.raises(ConnectorError) as stage:
        connectors.run_stage("w1", "r1", "s1", {"calls": "echo", "params": {}})
    assert str(stage.value) == NEWER
    with pytest.raises(InstallError):
        packages.manifest("echo", "9.9.9")


def test_a_local_package_carries_the_newest_contract_and_is_reused(tmp_path: Path) -> None:
    packages = Packages(tmp_path / "data/packages", FIXTURES)
    drafted = {**read_manifest(echo(tmp_path, "")), "name": "drafted"}
    del drafted["install"]
    first = packages.install_local(drafted)
    assert packages.manifest("drafted", first.version)["contract"] == 2
    assert packages.install_local(drafted) == first


def test_the_index_reads_an_entry_contract_absent_as_1(tmp_path: Path) -> None:
    file = tmp_path / "index.toml"
    entry = """
[[packages]]
name = "echo"
description = "Says it back."
git = "https://example.org/echo.git"
ref = "echo-v2.0.0"
permissions = []
"""
    file.write_text(entry + entry.replace("[[packages]]", "[[packages]]\ncontract = 2"))
    assert [e["contract"] for e in read_index(str(file))] == [1, 2]
