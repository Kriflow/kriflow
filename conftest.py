"""Session guard and fakes for every test path."""

import os
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from kriflow_connectors import packages


def pytest_configure(config: pytest.Config) -> None:
    """An empty Default List (issue 585) unless a test names its own: no test clones GitHub."""
    if "KRIFLOW_DEFAULT_PACKAGES" not in os.environ:
        file = Path(tempfile.mkdtemp()) / "no-defaults.toml"
        file.write_text('git = "nowhere"\npackages = []\n')
        os.environ["KRIFLOW_DEFAULT_PACKAGES"] = str(file)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail the run when a non-daemon thread outlives it: Python joins those at exit, so the
    process would hang after the summary."""
    others = [t for t in threading.enumerate() if t is not threading.main_thread() and not t.daemon]
    for thread in others:
        thread.join(5)
    if left := [t.name for t in others if t.is_alive()]:
        sys.stderr.write(f"\nnon-daemon threads outlived the session: {', '.join(left)}\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(scope="session", autouse=True)
def fake_node_installs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A package's install step gets `npm` and `npx` that only append what they were asked to
    `fake-install.log` where they run, so every app a test starts does not fetch remotion_render's
    Remotion CLI and Chrome (800 MB) into its default packages. An opted-in real build
    (KRIFLOW_REAL_BUILD) installs for real."""
    if os.environ.get("KRIFLOW_REAL_BUILD"):
        yield
        return
    bin_dir = tmp_path_factory.mktemp("fake-node")
    for tool in ("npm", "npx"):
        (bin_dir / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> fake-install.log\n')
        (bin_dir / tool).chmod(0o755)
    real = packages._install_env

    def install_env() -> dict[str, str]:
        env = real()
        return {**env, "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(packages, "_install_env", install_env)
        yield
