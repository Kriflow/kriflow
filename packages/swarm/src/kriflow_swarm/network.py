"""A session that died because the model API was unreachable (Wi-Fi down, DNS gone) is not a
failed attempt: whoever ran it waits for the network and resumes the same session. `transient`
tells such an error from an agent or task failure; `NetworkWait` waits, with backoff, until a
cheap probe reaches the API again, for at most `SWARMIFY_NETWORK_WAIT_HOURS` (6 by default)."""

import asyncio
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from kriflow_swarm.ports import AgentRuntime, SessionEnd, SessionHooks, SessionInit, SessionSpec

MARKERS = (
    "EAI_AGAIN",
    "ENOTFOUND",
    "ECONNRESET",
    "ECONNREFUSED",
    "ETIMEDOUT",
    "ENETUNREACH",
    "ENETDOWN",
    "EHOSTUNREACH",
    "can't reach the api server",
    "api error: connection error",
    "api error: request timed out",
    "fetch failed",
    "socket hang up",
)
"""What the CLI and its HTTP client say when the API is out of reach, matched without case."""
TYPES = {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout"}
"""Connection error classes of the Anthropic and httpx clients, by name: neither is our import.
The Agent SDK's CLIConnectionError is not one: it means the local CLI, not the network."""
HOURS = 6.0
"""How long a session waits for the network by default; `SWARMIFY_NETWORK_WAIT_HOURS` sets it."""
NOTICE_AFTER = 600.0
"""Seconds of waiting after which the Needs-you page says so."""
ASKED = 120.0
"""How long a session someone waits on (the Run Assistant's turn) waits for the network."""


LIMITS = (
    "hit your session limit",
    "hit your usage limit",
    "hit your weekly limit",
    "usage limit reached",
)
"""What the CLI says when the subscription's usage limit is spent, matched without case: not a
failed session either, it resumes once the limit resets."""
RESET = re.compile(
    r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([^)]+)\))?", re.I
)
UNSTATED = 1800.0
"""Seconds to wait for a usage limit whose message names no reset time."""
SLACK = 60.0


def _text(error: BaseException | str | None) -> str:
    texts, seen = [], error
    while isinstance(seen, BaseException):
        texts.append(str(seen))
        seen = seen.__cause__ or seen.__context__
    return " ".join(texts) if texts else str(error or "")


def reset_in(error: BaseException | str | None, now: datetime | None = None) -> float | None:
    """Seconds until the usage limit `error` reports resets ("resets 10:50am (UTC)", the next such
    time in its zone, plus a minute), `UNSTATED` when it names no time; None when it is not a
    usage limit."""
    text = _text(error)
    if not any(m in text.lower() for m in LIMITS):
        return None
    found = RESET.search(text)
    if not found:
        return UNSTATED
    hour, minute, half, zone = found.groups()
    hour = int(hour) % 12 + (12 if (half or "").lower() == "pm" else 0) if half else int(hour)
    try:
        tz = ZoneInfo(zone) if zone else UTC
    except (KeyError, ValueError):  # ZoneInfoNotFoundError is a KeyError
        tz = UTC
    here = (now or datetime.now(UTC)).astimezone(tz)
    at = here.replace(hour=hour % 24, minute=int(minute or 0), second=0, microsecond=0)
    if at <= here:
        at += timedelta(days=1)
    return (at - here).total_seconds() + SLACK


def transient(error: BaseException | str | None) -> bool:
    """True when `error` (an exception, its causes, or a session's error result) says the model
    API could not be reached, as opposed to the agent or its task failing."""
    texts, seen = [], error
    while isinstance(seen, BaseException):
        if isinstance(seen, ConnectionError | socket.gaierror) or type(seen).__name__ in TYPES:
            return True
        texts.append(str(seen))
        seen = seen.__cause__ or seen.__context__
    text = " ".join(texts) if texts else str(error or "")
    return any(m.lower() in text.lower() for m in (*MARKERS, *LIMITS))


def reachable(timeout: float = 5.0) -> bool:
    """A DNS lookup and a TCP connect to the model API (`ANTHROPIC_BASE_URL`, else Anthropic's)."""
    url = urlsplit(os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com")
    try:
        socket.create_connection((url.hostname or "", url.port or 443), timeout).close()
        return True
    except OSError:
        return False


def _span(seconds: float) -> str:
    return f"{seconds / 3600:g} h" if seconds >= 3600 else f"{seconds / 60:g} min"


def _limit() -> float:
    return float(os.environ.get("SWARMIFY_NETWORK_WAIT_HOURS") or HOURS) * 3600


class NetworkDown(Exception):
    """The network did not come back within the wait's limit."""


@dataclass(frozen=True)
class Waiting:
    """An agent waiting for the network, since when, after which error."""

    world_id: str
    run_id: str
    stage_id: str
    agent: str
    since: datetime
    error: str


@dataclass
class NetworkWait:
    first: float = 30.0
    most: float = 300.0
    limit: float = field(default_factory=_limit)
    """Seconds one session may spend waiting in all."""
    probe: Callable[[], bool] = reachable
    sleep: Callable[[float], None] = time.sleep
    tick: float = 5.0
    """How often a sleep looks at `stop`, so a pause or an abort is not held up."""

    def wait(
        self,
        spent: float = 0.0,
        stop: Callable[[], object] = lambda: None,
        until: float | None = None,
    ) -> float:
        """Sleep `first` seconds, doubling up to `most`, until the probe reaches the API; the
        seconds slept. With `until`, sleep that long instead (a usage limit's reset) and probe
        nothing. Returns early once `stop()` is truthy. Raises NetworkDown when `spent` (what the
        session waited before) plus this wait passes `limit`."""
        if until is not None:
            if spent + until > self.limit:
                raise NetworkDown(
                    f"the usage limit resets in {_span(until)}, past the wait's limit"
                )
            slept = 0.0
            while slept < until:
                if stop():
                    return slept
                step = min(self.tick, until - slept)
                self.sleep(step)
                slept += step
            return slept
        slept, delay = 0.0, self.first
        while True:
            end = slept + delay
            while slept < end:
                if stop():
                    return slept
                step = min(self.tick, end - slept)
                self.sleep(step)
                slept += step
            if self.probe():
                return slept
            if spent + slept >= self.limit:
                raise NetworkDown(f"the network was still down after {_span(self.limit)}")
            delay = min(delay * 2, self.most)


class _Seen:
    """Passes the hooks through, keeping the session id the runtime started."""

    def __init__(self, hooks: SessionHooks) -> None:
        self.hooks, self.session_id = hooks, None

    def started(self, init: SessionInit) -> None:
        self.session_id = init.session_id
        self.hooks.started(init)

    def before_tool(self, tool: str, input: dict[str, Any], ref: str | None = None) -> str | None:
        return self.hooks.before_tool(tool, input, ref)

    def said(self, text: str) -> None:
        self.hooks.said(text)

    def tool_result(self, ref: str, text: str, is_error: bool) -> None:
        self.hooks.tool_result(ref, text, is_error)

    def inbox(self) -> list[tuple[str, bool]]:
        return self.hooks.inbox()

    def cost(self, usd: float, tokens: dict[str, int] | None = None) -> str | None:
        return self.hooks.cost(usd, tokens)


class Patient:
    """An AgentRuntime that, when a session dies for want of the network, waits for it and
    resumes the same session; for sessions run outside a swarm stage (the Run Assistant), which
    keep no session ids of their own. Raises NetworkDown once `network` gives up."""

    def __init__(self, runtime: AgentRuntime, network: NetworkWait) -> None:
        self.runtime, self.network = runtime, network

    async def run(self, spec: SessionSpec, hooks: SessionHooks) -> SessionEnd:
        spent = 0.0
        while True:
            seen = _Seen(hooks)
            try:
                end = await self.runtime.run(spec, seen)
                if not (end.is_error and transient(end.result)):
                    return end
                seen_error: BaseException | str | None = end.result
            except Exception as exc:
                if not transient(exc):
                    raise
                seen_error = exc
            until = reset_in(seen_error)
            spent += await asyncio.to_thread(self.network.wait, spent, lambda: None, until)
            spec = replace(spec, resume=seen.session_id or spec.resume)
