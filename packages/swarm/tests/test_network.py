"""Telling a network outage from a failed session, and waiting for the network with backoff."""

import asyncio
import socket
from pathlib import Path

import pytest
from claude_agent_sdk import CLIConnectionError, ProcessError

from kriflow_swarm import SdkRuntime
from kriflow_swarm.fake import Answer, Die, Say, Script
from kriflow_swarm.network import NetworkDown, NetworkWait, Patient, transient
from kriflow_swarm.ports import SessionInit, SessionSpec


class APIConnectionError(Exception):
    """Named like the Anthropic client's, which is matched by name."""


@pytest.mark.parametrize(
    "error",
    [
        "API Error: Can't reach the API server (EAI_AGAIN)",
        RuntimeError("getaddrinfo ENOTFOUND api.anthropic.com"),
        ProcessError("Command failed", 1, "read ECONNRESET"),
        "API Error: Connection error.",
        "API Error: Request timed out.",
        "connect ETIMEDOUT 160.79.104.10:443",
        ConnectionResetError("reset by peer"),
        socket.gaierror(-3, "Temporary failure in name resolution"),
        APIConnectionError("Connection error."),
    ],
)
def test_a_network_error_is_transient(error: object) -> None:
    assert transient(error)  # type: ignore[arg-type]


def test_a_cause_that_is_a_network_error_makes_it_transient() -> None:
    try:
        try:
            raise OSError("EAI_AGAIN")
        except OSError as inner:
            raise RuntimeError("the session died") from inner
    except RuntimeError as exc:
        assert transient(exc)


@pytest.mark.parametrize(
    "error",
    [
        "tsc found 3 errors",
        RuntimeError("the claude process died"),
        "API Error: 400 invalid_request_error",
        CLIConnectionError("Claude Code not found"),
        None,
    ],
)
def test_a_task_or_agent_failure_is_not(error: object) -> None:
    assert not transient(error)  # type: ignore[arg-type]


def _wait(up_after: int, limit: float = 6 * 3600) -> tuple[NetworkWait, list[float]]:
    """A wait whose probe fails `up_after` times then reaches the API; its sleeps recorded."""
    probes, slept = iter([False] * up_after + [True] * 100), []
    return NetworkWait(limit=limit, probe=lambda: next(probes), sleep=slept.append, tick=1e9), slept


def test_the_wait_backs_off_from_30_seconds_doubling_to_5_minutes() -> None:
    wait, slept = _wait(up_after=6)
    assert wait.wait() == sum(slept)
    assert slept == [30, 60, 120, 240, 300, 300, 300]


def test_the_wait_gives_up_past_its_limit_counting_what_was_waited_before() -> None:
    wait, slept = _wait(up_after=1000, limit=3600)
    with pytest.raises(NetworkDown, match="1 h"):
        wait.wait(spent=600)
    assert 3000 <= sum(slept) < 3000 + 300


def test_a_stop_ends_the_wait_early() -> None:
    slept: list[float] = []
    wait = NetworkWait(probe=lambda: False, sleep=slept.append, tick=5)
    assert wait.wait(stop=lambda: len(slept) >= 2) == 10


class _Hooks:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def started(self, init: SessionInit) -> None:
        self.ids.append(init.session_id)

    def said(self, text: str) -> None: ...

    def tool_result(self, ref: str, text: str, is_error: bool) -> None: ...

    def inbox(self) -> list[tuple[str, bool]]:
        return []

    def before_tool(self, tool: str, input: dict, ref: str | None = None) -> str | None:
        return None

    def cost(self, usd: float, tokens: dict[str, int] | None = None) -> str | None:
        return None


SPEC = SessionSpec(system_prompt="s", prompt="Why?", cwd=Path("."), plugin=None, tools=[],
                   allowed=[], output_schema={"type": "object"})  # fmt: skip


def test_a_patient_runtime_waits_and_resumes_the_session_it_lost() -> None:
    offline = Die("API Error: Can't reach the API server (EAI_AGAIN)")
    script = Script([Say("Looking."), offline], [offline], [Answer({"reply": "Back."})])
    probes = iter([False, True, True])
    wait = NetworkWait(probe=lambda: next(probes), sleep=lambda _: None)
    hooks = _Hooks()
    end = asyncio.run(Patient(SdkRuntime(transport=script.transport), wait).run(SPEC, hooks))
    assert end.output == {"reply": "Back."}
    first, *again = script.sessions
    assert first.resume is None and [s.resume for s in again] == [hooks.ids[0]] * 2


def test_a_patient_runtime_passes_any_other_failure_on() -> None:
    wait = NetworkWait(probe=lambda: pytest.fail("no wait"), sleep=lambda _: None)
    runtime = Patient(SdkRuntime(transport=Script([Die()]).transport), wait)
    with pytest.raises(Exception, match="process died"):
        asyncio.run(runtime.run(SPEC, _Hooks()))


def test_a_usage_limit_is_waited_out_until_the_reset_it_names() -> None:
    from datetime import UTC, datetime

    from kriflow_swarm.network import reset_in

    said = "You've hit your session limit · resets 10:50am (UTC)"
    now = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)
    assert transient(said)
    assert reset_in(said, now) == (2 * 3600 + 50 * 60) + 60
    assert (
        reset_in("You've hit your usage limit · resets 3pm (Europe/London)", now) == 7 * 3600 + 60
    )
    assert reset_in("You've hit your session limit · resets 7am (UTC)", now) == 23 * 3600 + 60
    assert reset_in("Claude usage limit reached.", now) == 1800
    assert reset_in("API Error: Connection error.", now) is None
    slept: list[float] = []
    wait = NetworkWait(probe=lambda: False, sleep=slept.append, tick=600)
    assert wait.wait(until=1500) == 1500 and slept == [600, 600, 300]
    with pytest.raises(NetworkDown, match="usage limit"):
        NetworkWait(limit=3600, sleep=slept.append).wait(until=4000)
