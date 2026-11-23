"""Swarm context: the Whiteboard of a swarm stage, where agents Claim Tasks (ADR 0017), and the
Planner, Worker and Judge that run it through the AgentRuntime port (ADR 0010, 0016).

Runs and Memory are other contexts, so callers hand in the world, run and stage ids and the
composition root hands in the files and graph the worker uses.
"""

from kriflow_swarm import build
from kriflow_swarm.model import (
    HEARTBEAT_TIMEOUT,
    OPERATOR,
    Agent,
    Attempt,
    LogEntry,
    Note,
    Refused,
    Task,
    TaskStatus,
    Verdict,
)
from kriflow_swarm.ports import (
    AgentRuntime,
    AgentTool,
    SessionEnd,
    SessionHooks,
    SessionInit,
    SessionLost,
    SessionSpec,
    Storage,
    ToolError,
    tool_id,
)
from kriflow_swarm.projection import MAX_LOOPS
from kriflow_swarm.sdk import SdkRuntime
from kriflow_swarm.sqlite import MIGRATIONS, SqliteStorage
from kriflow_swarm.worker import (
    DEFAULT_CAST,
    GRAPH_TOOLS,
    JUDGE,
    LIST_TOOLS,
    PLANNER,
    WORKER,
    CastEntry,
    Files,
    Graphs,
    Lists,
    StageError,
    Stopped,
    Swarm,
    allowlist,
)

__all__ = [
    "DEFAULT_CAST",
    "GRAPH_TOOLS",
    "HEARTBEAT_TIMEOUT",
    "JUDGE",
    "LIST_TOOLS",
    "MAX_LOOPS",
    "MIGRATIONS",
    "OPERATOR",
    "PLANNER",
    "WORKER",
    "Agent",
    "AgentRuntime",
    "AgentTool",
    "Attempt",
    "CastEntry",
    "Files",
    "Graphs",
    "Lists",
    "LogEntry",
    "Note",
    "Refused",
    "SdkRuntime",
    "SessionEnd",
    "SessionHooks",
    "SessionInit",
    "SessionLost",
    "SessionSpec",
    "SqliteStorage",
    "StageError",
    "Stopped",
    "Storage",
    "Swarm",
    "Task",
    "TaskStatus",
    "ToolError",
    "Verdict",
    "allowlist",
    "build",
    "tool_id",
]
