"""What a session costs the Budget and records on its agent (issue 536): its token counts, a
resumed session counting only its own spend, and only the allowed built-in tools offered."""

from pathlib import Path

from kriflow_swarm import OPERATOR, SdkRuntime, SqliteStorage, Swarm, tool_id
from kriflow_swarm.fake import LAST, Answer, Call, Say, Script
from swarm_db import DiskFiles

W, R, S = "world_1", "run_1", "stage_1"
STEP = {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 300}


def _swarm(board: SqliteStorage, tmp_path: Path, script: Script) -> tuple[Swarm, Path]:
    folder = tmp_path / W
    folder.mkdir()
    return Swarm(board, SdkRuntime(transport=script.transport), DiskFiles(tmp_path)), folder  # type: ignore[arg-type]


def test_the_agent_keeps_its_token_counts_and_sees_only_its_allowed_built_ins(
    board: SqliteStorage, tmp_path: Path
) -> None:
    board.add_task(W, R, S, "Pull top shorts", added_by=OPERATOR)
    work = [
        Call(tool_id("query_graph"), {"question": "?"}),
        Call(tool_id("claim_task")),
        Call(tool_id("finish_task"), {"task_id": LAST, "note": "Done."}),
        Say("Done."),
    ]
    script = Script(work, [Answer({"passed": True, "reasons": ["ok"]})], usage=STEP)
    swarm, folder = _swarm(board, tmp_path, script)
    swarm.run_stage(W, R, S, "research", {"goal": "g", "done_when": "d"}, folder)

    worker = next(a for a in board.list_agents(R) if a.role == "worker")
    assert worker.tokens == {
        "input_tokens": 30,
        "output_tokens": 6,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 900,
    }
    # Bash, Agent and the rest are always refused here, so they are never offered; MCP tools are
    # not built-ins and stay, through the allowlist.
    offered = script.sessions[0].tools
    assert offered == ["Skill", "Read", "StructuredOutput", "ToolSearch"]
    assert any(t.startswith("mcp__") for t in script.sessions[0].allowed_tools)


def test_a_resumed_session_adds_only_its_own_spend(board: SqliteStorage, tmp_path: Path) -> None:
    # The first answer is unusable, so a replacement resumes the session. Its result total ($0.10)
    # includes the $0.05 the first already reported, which counts once.
    script = Script([Answer({"n": "x"})], [Answer({"n": 2})], cost=0.05)
    swarm, folder = _swarm(board, tmp_path, script)

    def parse(output: object) -> int:
        n = output["n"] if isinstance(output, dict) else None
        if not isinstance(n, int):
            raise ValueError("n must be a number")
        return n

    assert swarm.ask(W, R, S, "retro", folder, "retro", "sys", "go", {}, parse) == 2
    first, second = script.sessions
    (agent,) = board.list_agents(R)
    assert second.resume == agent.session_id
    assert agent.cost_usd == 0.1 and agent.session_total_usd == 0.1


def test_a_new_session_forgets_the_total_of_the_one_before(board: SqliteStorage) -> None:
    board.agent(W, R, S, "worker-1", "worker")
    board.update_agent(S, "worker-1", session_id="a")
    assert board.update_agent(S, "worker-1", session_total_usd=1.5).session_total_usd == 1.5
    assert board.update_agent(S, "worker-1", session_id="a").session_total_usd == 1.5
    assert board.update_agent(S, "worker-1", session_id="b").session_total_usd is None
