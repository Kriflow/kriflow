"""The fake ConnectorTransport: records every start and call, answers with declared outputs."""

import os
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from kriflow_connectors.model import Connector, ConnectorError, Tool
from kriflow_connectors.packages import manifest_file

FIXTURES = Path(__file__).parents[2] / "tests/fixtures/packages"
"""The fixture packages of the core's tests."""
FIXTURE_DEFAULTS = ("fake_publish", "kit", "sketch")
"""The Default List of the core's API and end-to-end tests (issue 586): fixture packages standing
in for the real ones (`fake_publish` for a publish platform, `kit` for a Templates render,
`sketch` for a Build Session), so no core test needs a real package or GitHub."""


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    who = ["-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "tag.gpgSign=false"]
    done = subprocess.run(
        ["git", *who, *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def tag_package(repo: Path, folder: Path) -> str:
    """Release the package at `folder` in the packages repository `repo` (made when missing):
    copied to `<name>/`, committed and tagged `<name>-v<version>`, as Kriflow/packages does.
    The tag."""
    manifest = tomllib.loads(manifest_file(folder).read_text())
    name, tag = manifest["name"], f"{manifest['name']}-v{manifest['version']}"
    if not (repo / ".git").is_dir():
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-q")
    shutil.rmtree(repo / name, ignore_errors=True)
    skip = shutil.ignore_patterns(".venv", "node_modules", "__pycache__", ".swarmify-install.json")
    shutil.copytree(folder, repo / name, ignore=skip)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", tag, "--allow-empty")
    _git(repo, "tag", tag)
    return tag


def default_list(where: Path, packages: Mapping[str, Path]) -> Path:
    """A Default List at `where/defaults.toml` naming each package of `packages` (name to its
    folder) at its release tag in a local packages repository `where/repo`, for
    `SWARMIFY_DEFAULT_PACKAGES`. The list's file."""
    repo = where / "repo"
    tags = {name: tag_package(repo, folder) for name, folder in packages.items()}
    lines = [f'git = "{repo}"', "packages = ["]
    lines += [f'  {{ name = "{n}", ref = "{t}" }},' for n, t in tags.items()]
    file = where / "defaults.toml"
    file.write_text("\n".join([*lines, "]", ""]))
    return file


Answer = dict[str, Any] | BaseException | Callable[[dict[str, Any]], dict[str, Any]]


class FakeTransport:
    """`tools` maps a connector name to what it lists; a connector missing there fails to start.
    `outputs` maps a tool name to its answers in order (the last repeats), or `connector.tool`
    for one connector's answers to a tool several offer, which win. An exception answer
    is raised; a BaseException stands in for a crash; a function answers with what it returns
    for the call's arguments. Undeclared tools answer `{"ok": True}`."""

    def __init__(
        self,
        tools: dict[str, list[Tool]] | None = None,
        outputs: dict[str, list[Answer]] | None = None,
    ) -> None:
        self.tools = tools or {}
        self.outputs = outputs or {}
        self.starts: list[tuple[str, dict[str, str]]] = []
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
        self.versions: list[str | None] = []
        """The package version of each call's connector, beside `calls`."""

    def list_tools(self, connector: Connector, env: dict[str, str]) -> list[Tool]:
        self.starts.append((connector.name, env))
        if connector.name not in self.tools:
            raise ConnectorError(f"{connector.name}: could not start")
        return self.tools[connector.name]

    @contextmanager
    def session(
        self, connector: Connector, env: dict[str, str]
    ) -> Iterator[Callable[[str, dict[str, Any]], dict[str, Any]]]:
        yield lambda tool, arguments: self.call(connector, tool, arguments, env)

    def call(
        self, connector: Connector, tool: str, arguments: dict[str, Any], env: dict[str, str]
    ) -> dict[str, Any]:
        self.calls.append((connector.name, tool, arguments, env))
        self.versions.append(connector.version)
        answers = self.outputs.get(f"{connector.name}.{tool}") or self.outputs.get(tool)
        if not answers:
            return {"ok": True}
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer(arguments) if callable(answer) else answer
