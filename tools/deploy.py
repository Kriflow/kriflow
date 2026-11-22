"""Deploy the box from its checkout (README, Deployment), safe to run at any time:

    python3 tools/deploy.py [--backup LABEL] [--seconds N] [--cut]
    python3 tools/deploy.py --web-only

Builds every image while the api still serves, drains the api (no new stage starts; those at
work finish, up to --seconds, SWARMIFY_DRAIN_SECONDS by default), stops the api, recreates the
services and waits until the new api serves. Runs at a gate, waiting for a time, on a child run
or queued never hold it up; they go on after the update. At the limit it cancels the drain and
stops, deploying nothing, unless --cut: then the stages still at work are stopped and run again
after the update. The api stops before the sandbox, so a cut connector stage replays instead of
failing. --backup takes the slim backup first (tools/backup_data.py, only when the deploy
changes stored data). --web-only recreates only web, with no drain.

SWARMIFY_API_TOKEN must hold an API token of the whole MCP server (Tokens page): the drain is
asked through `swarmify-drain` inside the api container. Pull the code first. Host Python,
stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ["api", "web", "sandbox", "graph-writer"]
HEALTH_SECONDS = 600


class Failed(Exception):
    pass


def sh(cmd: list[str]) -> tuple[int, str]:
    """Runs in the checkout; a drain call's output is read, every other command's is shown."""
    quiet = "exec" in cmd
    done = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE if quiet else None, text=True)
    return done.returncode, done.stdout or ""


def deploy(
    argv: list[str],
    run: Callable[[list[str]], tuple[int, str]] = sh,
    sleep: Callable[[float], None] = time.sleep,
    say: Callable[[str], None] = print,
    root: Path = ROOT,
) -> int:
    parser = argparse.ArgumentParser(prog="deploy.py")
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument("--backup", metavar="LABEL")
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--cut", action="store_true")
    args = parser.parse_args(argv)
    compose = ["docker", "compose", "-f", "compose.yaml"]
    if (root / "compose.gpu.yaml").exists():
        compose += ["-f", "compose.gpu.yaml"]

    def must(cmd: list[str], why: str) -> None:
        if run(cmd)[0]:
            raise Failed(why)

    def drain(*words: str) -> dict:
        cmd = [*compose, "exec", "-T", "-e", "SWARMIFY_API_TOKEN", "api", "swarmify-drain"]
        code, out = run([*cmd, *words])
        if code:
            raise Failed(
                f"the api did not answer `swarmify-drain {words[0]}` (is SWARMIFY_API_TOKEN set?)"
            )
        return json.loads(out)

    def named(state: dict) -> list[str]:
        return [f"{w['run_name']}: {w['stage']} ({w['type']})" for w in state["at_work"]]

    try:
        if args.backup:
            backup = [sys.executable, "tools/backup_data.py", args.backup]
            must([*backup, "--dest", "/data", "--keep", "3"], "the backup failed; nothing deployed")
        if args.web_only:
            must([*compose, "up", "-d", "--build", "--no-deps", "web"], "web did not deploy")
            say("Deployed web.")
            return 0
        drain("status")  # a missing or refused token stops here, before anything changes
        must([*compose, "build", *SERVICES], "the build failed; the api still serves as before")
        state = drain("start", *(["--seconds", str(args.seconds)] if args.seconds else []))
        waited: list[str] = []
        while state["state"] == "draining":
            waited += [n for n in named(state) if n not in waited]
            if not state["seconds_left"]:
                if not args.cut:
                    drain("cancel")
                    say("Stopped at the drain limit; nothing deployed. Still at work:")
                    say("\n".join(f"  {n}" for n in named(state)))
                    say("Deploy again later, or with --cut to stop them (they run again after).")
                    return 3
                say("Cut at the drain limit; these run again after the update:")
                say("\n".join(f"  {n}" for n in named(state)))
                break
            left, busy = state["seconds_left"], ", ".join(named(state))
            say(f"Draining, {left:.0f} s left, waiting for: {busy}")
            sleep(5)
            state = drain("status")
        cut = named(state) if state["state"] == "draining" else []
        must([*compose, "stop", "-t", "60", "api"], "the api did not stop")
        must([*compose, "up", "-d", "--no-build", *SERVICES], "the services did not start")
        deadline = time.monotonic() + HEALTH_SECONDS
        while True:
            try:
                if drain("status")["state"] == "serving":
                    break
            except (Failed, ValueError):
                pass
            if time.monotonic() > deadline:
                raise Failed("the new api did not answer; see `docker compose logs api`")
            sleep(3)
    except Failed as exc:
        say(f"Deploy failed: {exc}")
        return 1
    finished = [n for n in waited if n not in cut]
    say("Deployed. Waited for: " + (", ".join(finished) or "nothing, the api was idle") + ".")
    if cut:
        say("Cut, running again now: " + ", ".join(cut) + ".")
    return 0


if __name__ == "__main__":
    sys.exit(deploy(sys.argv[1:]))
