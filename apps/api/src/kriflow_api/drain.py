"""The Drain over HTTP (Safe Deploys): start, read and cancel it at `/api/drain`, and the
`swarmify-drain` console script that calls it from inside the api container, so a deploy needs
nothing on the box but docker. A session or an API token of the whole MCP server (Tokens page)
signs it in (auth.py); the script sends `SWARMIFY_API_TOKEN`."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field

from kriflow_api.upkeep import Notice
from kriflow_runs import DRAIN_SECONDS, Drain, Runner

PATH = "/api/drain"
UPDATING = (
    "Updating. Stages already at work finish first; new ones start after the update, in a few"
    " minutes."
)


class DrainStart(BaseModel):
    seconds: float | None = Field(None, ge=0, le=24 * 3600)
    """How long to wait for the stages at work; SWARMIFY_DRAIN_SECONDS when left out."""


def with_updating(runner: Runner, source: Callable[[], list[Notice]]) -> Callable[[], list[Notice]]:
    """`source`'s Needs-you notices, led by Updating while the api drains (the next process
    serves, so it goes)."""

    def notices() -> list[Notice]:
        if runner.drain_state().state == "serving":
            return source()
        return [Notice(kind="updating", text=UPDATING), *source()]

    return notices


def router(runner: Runner, limit: float = DRAIN_SECONDS) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["drain"])

    @api.get("/drain")
    def read() -> Drain:
        """serving, draining (with the stages still at work) or drained."""
        return runner.drain_state()

    @api.post("/drain")
    def start(body: DrainStart | None = None) -> Drain:
        """Start no new stages; those at work finish. A drain under way keeps its limit."""
        seconds = body.seconds if body and body.seconds is not None else limit
        return runner.drain(seconds)

    @api.delete("/drain")
    def cancel() -> Drain:
        """Serve again: stages that waited start now."""
        return runner.cancel_drain()

    return api


def main(argv: list[str] | None = None) -> None:
    """`swarmify-drain status|start|cancel [--seconds N]`: prints the drain as JSON."""
    parser = argparse.ArgumentParser(prog="swarmify-drain")
    parser.add_argument("action", choices=["status", "start", "cancel"])
    parser.add_argument("--seconds", type=float)
    args = parser.parse_args(argv)
    method = {"status": "GET", "start": "POST", "cancel": "DELETE"}[args.action]
    body = json.dumps({"seconds": args.seconds}).encode() if args.action == "start" else None
    request = urllib.request.Request(
        os.environ.get("SWARMIFY_DRAIN_URL", "http://127.0.0.1:8000") + PATH,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    if token := os.environ.get("SWARMIFY_API_TOKEN"):
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(response.read().decode())
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"swarmify-drain: {exc}")
