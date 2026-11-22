"""The Swarm language for the whiteboard: Tasks of one swarm stage, their Claims and notes."""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

TaskStatus = Literal["open", "claimed", "done"]
HEARTBEAT_TIMEOUT = timedelta(minutes=2)
"""A claimed task whose last heartbeat is older than this returns to open."""
OPERATOR = "operator"
"""`added_by` of a task a person added by hand."""


@dataclass(frozen=True)
class Note:
    author: str
    body: str
    ts: str


@dataclass(frozen=True)
class Task:
    id: str
    world_id: str
    run_id: str
    stage_id: str
    title: str
    body: str
    status: TaskStatus
    owner: str | None
    added_by: str
    heartbeat_at: str | None
    created_at: str
    updated_at: str
    depends_on: list[str] = field(default_factory=list)
    """Tasks that must be done before this one can be claimed (beads' `blocks`)."""
    notes: list[Note] = field(default_factory=list)


class Refused(ValueError):
    """The whiteboard refused the action, for example a claim on a task someone else holds."""


AgentRole = Literal["planner", "worker", "judge"]
AgentStatus = Literal["working", "done", "failed"]
LogKind = Literal["session", "tool", "denied", "result", "error", "text", "output", "message"]


@dataclass(frozen=True)
class Agent:
    """One agent of a swarm stage. `session_id` is its Agent SDK session, so a replacement agent
    resumes it instead of starting over. `cost_usd` is the runtime's estimate, not billing."""

    id: str
    world_id: str
    run_id: str
    stage_id: str
    name: str
    role: AgentRole
    status: AgentStatus
    session_id: str | None
    cost_usd: float
    created_at: str
    updated_at: str
    skills: list[str] = field(default_factory=list)
    """What the session's init message said loaded from the world's skills plugin."""
    model: str | None = None
    """The model its latest session ran on, as the session reported it."""


@dataclass(frozen=True)
class LogEntry:
    """One line of a stage's live log: a session starting or ending, one tool call the hooks
    saw, allowed (`tool`) or refused (`denied`, with the reason in `detail`), what the agent
    said (`text`), or an allowed call's result (`output`: a capped excerpt in `detail`, its
    full size and error flag in `input`). `ref` is the tool use id pairing a call with its
    output."""

    seq: int
    agent: str
    stage_id: str
    kind: LogKind
    ts: str
    tool: str | None = None
    input: dict[str, Any] | None = None
    detail: str = ""
    ref: str | None = None


@dataclass(frozen=True)
class Attempt:
    """One execution of a swarm stage, numbered per stage from 1 (issue 535). The first visit is
    Attempt 1; each send-back, Rerun or Follow-up that runs the stage again starts the next.
    `cause` is what started it, `{kind, note, by, at}`, None for the first or when an earlier
    stage was sent back. `spent_usd` is what its agents spent during it; `ended_at` is None
    while it runs. Its log lines are those from `started_at` to the next Attempt's start, and
    the artifacts it left are kept in the stage's `attempts/<number>` folder once another
    starts."""

    number: int
    started_at: str
    ended_at: str | None = None
    spent_usd: float = 0.0
    cause: dict[str, Any] | None = None


@dataclass(frozen=True)
class Verdict:
    """The Judge's pass or fail on one loop of a swarm stage, with reasons. A fail names the
    gap tasks it added to the whiteboard; a pass keeps the typed items it projected, so a retried
    stage projects the same artifacts again without asking the judge twice."""

    id: str
    world_id: str
    run_id: str
    stage_id: str
    loop: int
    """1 for the first verdict of the stage, one more for every gap loop after it."""
    passed: bool
    reasons: list[str]
    gaps: list[str]
    items: list[dict[str, Any]]
    path: str
    """The verdict artifact, relative to the world folder."""
    created_at: str
