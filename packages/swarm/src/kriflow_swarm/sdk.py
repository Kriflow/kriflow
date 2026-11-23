"""The Agent SDK adapter of the AgentRuntime port (ADR 0010).

Each session gets the stage's in-process tools as one SDK MCP server, a PreToolUse hook that
asks SessionHooks about every call, the world folder as a local plugin and no host settings
(ADR 0016). Without a transport factory the SDK starts its bundled `claude` CLI; tests pass one
that returns a scripted transport, which the SDK drives exactly like the CLI.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import McpServerConfig

from kriflow_kernel.settings import setting
from kriflow_swarm.model import TOKENS
from kriflow_swarm.ports import (
    TOOL_SERVER,
    AgentTool,
    SessionEnd,
    SessionHooks,
    SessionInit,
    SessionLost,
    SessionSpec,
    ToolError,
)

INBOX_POLL = 0.5
"""How often, in seconds, a live session asks its hooks for messages people sent it."""
DEFAULT_MODEL = "claude-opus-5-5"
"""The model a session runs on when nothing more specific chose one. `KRIFLOW_MODEL` overrides
it."""


def default_model() -> str:
    return setting("MODEL", DEFAULT_MODEL)


# Dollars per million tokens of each model the operator can choose. The SDK prices only the whole
# session, at its result, and that total trues the per-step estimates up (it also holds what no
# step reports, such as a web search's own model calls). A resumed session's total includes what
# the session it resumed reported, so that part, when the spec knows it, is taken off; when it does
# not, it counts twice: the Budget errs high, never low.
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {
        "input_tokens": 4.0,
        "output_tokens": 20.0,
        "cache_creation_input_tokens": 5.0,
        "cache_read_input_tokens": 0.2,
    },
    "claude-sonnet-5-5": {
        "input_tokens": 2.0,
        "output_tokens": 10.0,
        "cache_creation_input_tokens": 2.5,
        "cache_read_input_tokens": 0.2,
    },
    "claude-haiku-4-5": {
        "input_tokens": 1.0,
        "output_tokens": 5.0,
        "cache_creation_input_tokens": 1.25,
        "cache_read_input_tokens": 0.1,
    },
}
MOST = max(PRICES.values(), key=lambda p: p["output_tokens"])
"""What a model missing from the table is priced at, so the Budget errs high."""


def price(model: str | None) -> dict[str, float]:
    """The model's prices; a variant of a known id (`claude-sonnet-5-5[1m]`) is priced as it."""
    return next((p for m, p in PRICES.items() if (model or "").startswith(m)), MOST)


def step_cost(usage: dict[str, Any] | None, model: str | None) -> float:
    """The estimated cost of one model step on `model` from its usage."""
    rates = price(model)
    return sum(float((usage or {}).get(k) or 0) * p for k, p in rates.items()) / 1e6


def step_tokens(usage: dict[str, Any] | None) -> dict[str, int]:
    return {k: int((usage or {}).get(k) or 0) for k in TOKENS}


HARNESS = ("StructuredOutput", "ToolSearch")
"""CLI mechanics every session needs whatever its allowlist: StructuredOutput delivers an
`output_format` answer and ToolSearch loads a deferred tool's schema. Denying either leaves the
session with no usable answer."""

NO_CONVERSATION = "No conversation found with session ID"
"""How the CLI refuses to resume a session it has no transcript for."""

STDERR_LINES = 20
"""The last stderr lines of a tool server that failed to start, for the stage log."""


def _stderr(config: dict[str, Any]) -> list[str]:
    """The last lines a server wrote to stderr, from the file its config names as `stderr`."""
    try:
        return Path(config["stderr"]).read_text(errors="replace").splitlines()[-STDERR_LINES:]
    except (KeyError, OSError):
        return []


TransportFactory = Callable[[ClaudeAgentOptions], Any]
"""Builds the SDK `Transport` for a session from its options (the SDK's own test seam)."""


def _sdk_tool(spec: AgentTool) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"content": [{"type": "text", "text": await spec.run(args)}]}
        except ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}

    return tool(spec.name, spec.description, spec.schema)(handler)


def result_text(content: str | list[dict[str, Any]] | None) -> str:
    """A tool result's text; an image or other non-text part shows as its type in brackets."""
    if not isinstance(content, list):
        return content or ""
    return "\n".join(
        str(c.get("text", "")) if c.get("type") == "text" else f"[{c.get('type')}]" for c in content
    )


def offered_tools(allowed: list[str]) -> list[str]:
    """The built-in tool names in an allowlist (not MCP tools or patterns), plus the harness."""
    named = [t for t in allowed if t.isalnum()]
    return list(dict.fromkeys([*named, *HARNESS]))


def options(
    spec: SessionSpec,
    hooks: SessionHooks,
    model: str | None = None,
    sandboxed_cli: str | None = None,
) -> ClaudeAgentOptions:
    async def pre_tool(data: Any, tool_use_id: str | None, _context: Any) -> Any:
        if data["tool_name"] in HARNESS:
            return {}
        ref = tool_use_id or data.get("tool_use_id")
        reason = hooks.before_tool(data["tool_name"], data.get("tool_input") or {}, ref)
        if reason is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    servers: dict[str, McpServerConfig] = {
        TOOL_SERVER: create_sdk_mcp_server(TOOL_SERVER, tools=[_sdk_tool(t) for t in spec.tools]),
        # standard MCP config dicts, without our `stderr` file
        **{n: {k: v for k, v in c.items() if k != "stderr"} for n, c in spec.servers.items()},  # type: ignore[misc]
    }
    return ClaudeAgentOptions(
        system_prompt=spec.system_prompt,
        cwd=spec.cwd,
        mcp_servers=servers,
        allowed_tools=[*spec.allowed, *HARNESS],
        # Offer only the built-in tools the allowlist grants: any other is always refused, and an
        # agent that sees one spends turns trying it. MCP tools are not built-ins, so stay offered.
        tools=offered_tools(spec.allowed),
        permission_mode="dontAsk",
        setting_sources=[],
        plugins=[{"type": "local", "path": str(spec.plugin)}] if spec.plugin else [],
        resume=spec.resume,
        max_turns=spec.max_turns,
        model=model,
        output_format=(
            {"type": "json_schema", "schema": spec.output_schema} if spec.output_schema else None
        ),
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool])]},
        sandbox=spec.sandbox,  # type: ignore[arg-type]  # the CLI's settings shape, a superset
        env=spec.env,
        cli_path=sandboxed_cli if spec.sandbox else None,
        # One stream message carries a whole tool result: a few full-size frames a builder
        # reads to check its work are several MB of base64, past the SDK's 1 MB default.
        max_buffer_size=64 * 1024 * 1024,
    )


class SdkRuntime:
    def __init__(
        self,
        transport: TransportFactory | None = None,
        model: str | None = None,
        sandboxed_cli: str | None = None,
    ):
        """`model` runs every session whose spec names none; the default model by default.
        `sandboxed_cli` is the CLI a session with an OS sandbox (a Build Session) runs on,
        where that sandbox can start: in Docker, one that runs it in the sandbox runner."""
        self.transport = transport
        self.model = model or default_model()
        self.sandboxed_cli = sandboxed_cli

    async def run(self, spec: SessionSpec, hooks: SessionHooks) -> SessionEnd:
        try:
            return await self._run(spec, hooks)
        except Exception as exc:
            if spec.resume and NO_CONVERSATION in str(exc):
                raise SessionLost(str(exc)) from exc
            raise

    async def _run(self, spec: SessionSpec, hooks: SessionHooks) -> SessionEnd:
        opts = options(spec, hooks, spec.model or self.model, self.sandboxed_cli)
        custom = self.transport(opts) if self.transport else None
        spent, steps, session = 0.0, set[str](), spec.resume or ""
        turns, output = 1, None  # turns sent and not answered: the prompt, each message after
        async with ClaudeSDKClient(opts, transport=custom) as client:

            async def deliver(sent: list[tuple[str, bool]]) -> None:
                """Send people's messages as the next user turns, stopping the turn first when
                one asks to. `turns` counts them before any await, so the session cannot end
                between taking them from the inbox and sending them."""
                nonlocal turns
                turns += len(sent)
                if any(interrupt for _, interrupt in sent):
                    await client.interrupt()
                for text, _ in sent:
                    await client.query(text)

            async def poll() -> None:
                while True:
                    await asyncio.sleep(INBOX_POLL)
                    if sent := hooks.inbox():
                        await deliver(sent)

            await client.query(spec.prompt)
            poller = asyncio.create_task(poll())
            try:
                async for msg in client.receive_messages():
                    if isinstance(msg, AssistantMessage | UserMessage) and isinstance(
                        msg.content, list
                    ):
                        for block in msg.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                hooks.said(block.text)
                            elif isinstance(block, ToolResultBlock):
                                hooks.tool_result(
                                    block.tool_use_id,
                                    result_text(block.content),
                                    bool(block.is_error),
                                )
                    if isinstance(msg, AssistantMessage):
                        # One model step arrives as one message per content block, each carrying
                        # the step's usage, so a step is charged once, by its message id.
                        step = msg.message_id or msg.uuid or str(len(steps))
                        if step in steps or not msg.usage:
                            continue
                        steps.add(step)
                        usd = step_cost(msg.usage, msg.model or opts.model)
                        spent += usd
                        if stop := hooks.cost(usd, step_tokens(msg.usage)):
                            # Leaving the client ends the CLI; the session id stays resumable.
                            return SessionEnd(
                                msg.session_id or session, spent, True, stop, stopped=stop
                            )
                    elif isinstance(msg, SystemMessage) and msg.subtype == "init":
                        session = msg.data["session_id"]
                        errors = msg.data.get("plugin_errors") or []
                        failed = {
                            s["name"]: _stderr(spec.servers.get(s["name"], {}))
                            for s in msg.data.get("mcp_servers") or []
                            if s.get("status") == "failed"
                        }
                        hooks.started(
                            SessionInit(
                                msg.data["session_id"],
                                list(msg.data.get("skills") or []),
                                [f"{e.get('plugin')}: {e.get('message')}" for e in errors],
                                msg.data.get("model") or opts.model or "",
                                failed,
                            )
                        )
                    elif isinstance(msg, ResultMessage):
                        # The newest structured answer wins (a message's reply may correct it);
                        # it outlives a later turn that gives none.
                        if msg.structured_output is not None:
                            output = msg.structured_output
                        turns -= 1
                        if turns > 0:
                            continue  # an interrupted turn, or one answered before a message's
                        if sent := hooks.inbox():  # sent as the turn ended
                            await deliver(sent)
                            continue
                        poller.cancel()
                        total, before = msg.total_cost_usd, spec.resumed_total
                        if total is None:
                            cost = spent
                        elif before is None:
                            cost = total
                        else:  # never below its own steps, should the remembered total be off
                            cost = max(total - before, spent)
                        hooks.cost(cost - spent)
                        return SessionEnd(
                            msg.session_id,
                            cost,
                            msg.is_error,
                            msg.result or "",
                            output,
                            total_usd=total,
                        )
            finally:
                poller.cancel()
        raise RuntimeError("the agent session ended without a result")
