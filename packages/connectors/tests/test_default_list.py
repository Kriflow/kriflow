"""The Default List (issue 585): default packages are pinned by tag and installed from git, trusted
and sharing the caches, nothing fetched when that version is installed; `python_script` stays a
folder of the core. A local git repository of tagged fixture packages stands in for
Kriflow/packages, through the `SWARMIFY_DEFAULT_PACKAGES` override."""

import json
import shutil
import tomllib
from pathlib import Path

import pytest

import swarmify_connectors.packages as packages_module
from swarmify_connectors import DEFAULT_LIST, Connectors, InstallError
from swarmify_connectors.fake import default_list, tag_package
from swarmify_connectors.packages import RECORD

FIXTURES = Path(__file__).parent / "fixtures/packages"
REPO = Path(__file__).parents[3]


@pytest.fixture
def listed(tmp_path: Path, connectors: Connectors, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A packages repository with echo and probe tagged at 1.0.0, listed as the defaults; the
    core is a checkout holding only `connectors/python_script`."""
    core = tmp_path / "core"
    shutil.copytree(REPO / "connectors/python_script", core / "connectors/python_script")
    connectors.packages.repo = core
    file = default_list(tmp_path / "packages", {n: FIXTURES / n for n in ("echo", "probe")})
    monkeypatch.setenv("SWARMIFY_DEFAULT_PACKAGES", str(file))
    return file


def record(connectors: Connectors, name: str, version: str) -> dict:
    return json.loads((connectors.packages.root / name / version / RECORD).read_text())


def test_a_fresh_install_installs_each_listed_default_from_its_tag_trusted_with_the_caches(
    connectors: Connectors, listed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared: list[tuple[str, bool]] = []
    real = packages_module.Packages._run_install

    def run_install(self, manifest, target, ours):  # type: ignore[no-untyped-def]
        shared.append((manifest["name"], ours))
        return real(self, manifest, target, ours)

    monkeypatch.setattr(packages_module.Packages, "_run_install", run_install)
    defaults = connectors.install_defaults()
    assert defaults == {"echo": "1.0.0", "probe": "1.0.0"}
    git = tomllib.loads(listed.read_text())["git"]
    echo = record(connectors, "echo", "1.0.0")
    assert (echo["source"], echo["ref"], echo["subfolder"]) == (git, "echo-v1.0.0", "echo")
    assert len(echo["commit"]) == 40
    assert record(connectors, "python_script", "1.0.0")["source"] == "connectors/python_script"
    assert ("echo", True) in shared  # its install step ran, sharing uv's and npm's caches
    assert (connectors.packages.root / "echo/1.0.0/installed.txt").is_file()


def test_a_listed_version_already_installed_is_never_fetched(
    connectors: Connectors, listed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connectors.install_defaults()

    def no_git(*args: str) -> str:
        raise AssertionError(f"git was called: {args}")

    monkeypatch.setattr(packages_module, "_git", no_git)
    assert connectors.install_defaults()["echo"] == "1.0.0"


def test_a_default_that_does_not_fetch_is_logged_and_the_others_install(
    connectors: Connectors, listed: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = listed.read_text().replace('"probe-v1.0.0"', '"probe-v9.9.9"')
    listed.write_text(text)
    assert connectors.install_defaults() == {"echo": "1.0.0"}
    assert "the default package probe 9.9.9 did not install" in caplog.text


def test_worlds_are_given_defaults_once_and_keep_their_pins_when_the_list_moves(
    connectors: Connectors, listed: Path
) -> None:
    connectors.install_defaults()
    connectors.offer_defaults("w1")
    assert connectors.enabled("w1")["echo"] == "1.0.0"
    tag_package(listed.parent / "repo", FIXTURES / "echo_2")  # echo 2.0.0, released
    listed.write_text(listed.read_text().replace('"echo-v1.0.0"', '"echo-v2.0.0"'))
    assert connectors.install_defaults()["echo"] == "2.0.0"
    assert [p.version for p in connectors.packages.list() if p.name == "echo"] == [
        "1.0.0",
        "2.0.0",
    ]
    connectors.offer_defaults("w1")
    connectors.offer_defaults("w2")
    assert connectors.enabled("w1")["echo"] == "1.0.0"  # the box never moves a pin
    assert connectors.enabled("w2")["echo"] == "2.0.0"


def test_a_box_with_its_defaults_installed_from_the_old_folder_fetches_and_moves_nothing(
    tmp_path: Path, connectors: Connectors, listed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The box before the Default List: every default installed from `connectors/<name>`, its
    worlds pinned. Started on a Default List at the same versions, it installs nothing."""
    old = connectors.packages.repo
    for name in ("echo", "probe"):
        shutil.copytree(FIXTURES / name, old / "connectors" / name)
        connectors.packages.install(f"connectors/{name}")
    connectors.install_defaults()
    connectors.offer_defaults("w1")
    connectors.disable("w1", "probe")
    before = (connectors.packages.list(), connectors.enabled("w1"))

    def no_git(*args: str) -> str:
        raise AssertionError(f"git was called: {args}")

    monkeypatch.setattr(packages_module, "_git", no_git)
    for name in ("echo", "probe"):
        shutil.rmtree(old / "connectors" / name)  # the core no longer holds them
    assert connectors.install_defaults() == {
        "echo": "1.0.0",
        "probe": "1.0.0",
    }
    connectors.offer_defaults("w1")
    assert (connectors.packages.list(), connectors.enabled("w1")) == before
    assert record(connectors, "echo", "1.0.0")["source"] == "connectors/echo"


def test_a_local_default_list_is_held_to_the_package_sources(
    connectors: Connectors,
    listed: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SWARMIFY_PACKAGE_SOURCES", str(tmp_path / "elsewhere"))
    assert connectors.install_defaults() == {}
    assert "not in SWARMIFY_PACKAGE_SOURCES" in caplog.text


def test_the_shipped_list_pins_each_default_package_at_its_release_tag() -> None:
    shipped = tomllib.loads(DEFAULT_LIST.read_text())
    assert shipped["git"] == "https://github.com/Kriflow/packages.git"
    pins = {p["name"]: p["ref"] for p in shipped["packages"]}
    assert pins == {
        "blender_build": "blender_build-v1.1.3",
        "blender_render": "blender_render-v1.0.8",
        "hyperframes_build": "hyperframes_build-v1.0.3",
        "hyperframes_render": "hyperframes_render-v1.0.3",
        "meta": "meta-v1.3.1",
        "remotion_build": "remotion_build-v1.0.5",
        "remotion_render": "remotion_render-v1.0.10",
        "slack": "slack-v1.1.3",
        "speech": "speech-v1.3.2",
        "upload_post": "upload_post-v1.0.2",
        "youtube": "youtube-v1.6.1",
    }


def test_a_missing_default_list_installs_only_the_core_s_own(
    connectors: Connectors,
    listed: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SWARMIFY_DEFAULT_PACKAGES", str(listed.parent / "nope.toml"))
    assert connectors.install_defaults() == {}
    assert "the Default List" in caplog.text
    with pytest.raises(InstallError):
        connectors.packages.manifest("echo", "1.0.0")
