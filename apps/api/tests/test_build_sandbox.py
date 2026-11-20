"""Issue 332: a Build Session's Bash reads and writes its own build folder. The session's sandbox
rules (swarmify_swarm.build.sandbox) applied by sandbox-runtime, the library Claude Code's own
sandbox is, on this machine's OS sandbox (Seatbelt or bubblewrap): before the fix Seatbelt
refused `ls .` in the build folder while bubblewrap allowed it."""

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from swarmify_connectors.sandbox import LAUNCHER
from swarmify_swarm import build

RUNTIME = LAUNCHER.parent / "node_modules/@anthropic-ai/sandbox-runtime/dist/index.js"
SANDBOX = shutil.which("sandbox-exec") or shutil.which("bwrap")
RUN = """
import { spawnSync } from "node:child_process";
import { SandboxManager } from "%s";
const [rules, cwd, command] = JSON.parse(process.argv[1]);
const network = { allowedDomains: [], deniedDomains: [] };
const filesystem = { ...rules, denyWrite: [] };
await SandboxManager.initialize({ network, filesystem }, undefined, false);
const r = spawnSync("bash", ["-c", await SandboxManager.wrapWithSandbox(command)], { cwd });
process.stdout.write(r.stdout);
process.exit(r.status ?? 1);
"""


@pytest.mark.skipif(not (SANDBOX and shutil.which("node")), reason="no OS sandbox here")
def test_the_build_sessions_bash_reads_and_writes_its_build_folder_and_nothing_else(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    world = data / "worlds/w"
    folder, script = world / "runs/r/video/build", world / "runs/r/script/script.md"
    folder.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    (folder / "index.ts").write_text("starter\n")
    script.write_text("# Script\n")
    (data / ".env").write_text("KEY=1\n")
    rules = build.sandbox(folder, world, data, data / "npm-cache", [])["filesystem"]
    (data / "npm-cache").mkdir()

    def bash(command: str) -> subprocess.CompletedProcess[str]:
        args = json.dumps([rules, str(folder.resolve()), command])
        node = ["node", "--input-type=module", "-e", RUN % RUNTIME, args]
        return subprocess.run(node, capture_output=True, text=True, timeout=60)

    listed = bash("ls . && cat index.ts && echo x > made.txt && cat made.txt")
    assert listed.returncode == 0 and listed.stdout == "index.ts\nstarter\nx\n", listed
    assert bash(f"cat {script}").stdout == "# Script\n"  # the run's other stages
    assert bash(f"cat {data / '.env'}").returncode != 0
    assert bash(f"echo y > {script}").returncode != 0
    assert script.read_text() == "# Script\n"


@pytest.mark.skipif(
    not (SANDBOX and shutil.which("node") and shutil.which("pnpm")), reason="no sandbox or pnpm"
)
def test_two_builds_install_with_pnpm_from_one_shared_store(tmp_path: Path) -> None:
    """Each build's pnpm, in its session's sandbox, links its packages from the one store: a
    package file is the same inode in both build folders, not a copy per build."""
    data = tmp_path / "data"
    world = data / "worlds/w"
    store = data / "pnpm-store"
    dep = tmp_path / "dep"
    dep.mkdir()
    (dep / "package.json").write_text('{"name": "dep", "version": "1.0.0"}')
    (dep / "index.js").write_text("module.exports = 1;\n")
    tarball = tmp_path / "dep-1.0.0.tgz"  # a local tarball: the store takes it, no network
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(dep, "package")
    store.mkdir(parents=True)
    env = " ".join(f"{k}={v}" for k, v in build.pnpm_env(store).items())
    files = []
    for name in ("a", "b"):
        folder = world / f"runs/{name}/video/build"
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(
            json.dumps({"name": name, "private": True, "dependencies": {"dep": f"file:{tarball}"}})
        )
        rules = build.sandbox(folder, world, data, data / "npm-cache", [], (), store)
        rules = rules["filesystem"]
        rules["allowRead"].append(str(tarball.resolve()))
        build.PNPM_LOCKS.mkdir(mode=0o700, exist_ok=True)
        command = f"env {env} pnpm install --offline 2>&1"
        args = json.dumps([rules, str(folder.resolve()), command])
        node = ["node", "--input-type=module", "-e", RUN % RUNTIME, args]
        done = subprocess.run(node, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        files.append(folder / "node_modules/dep/index.js")
    a, b = (f.stat() for f in files)
    assert (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    assert files[0].resolve().is_relative_to(store.resolve())
