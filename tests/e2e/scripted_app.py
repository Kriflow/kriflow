"""The real app with a scripted agent CLI behind the Agent SDK, so e2e runs need no model.

Every swarm stage takes three sessions through the real SDK client, hooks and in-process
tools, each scripted by its role, told apart by its system prompt: the planner seeds one task;
the worker queries the graph, claims it, writes a note and an artifact and finishes it; the
judge passes with two ranked ideas. A stage named `steer` has a worker that waits mid-turn for
a person's message. Every run's retro session proposes nothing, or, with
`SWARMIFY_E2E_RETRO=propose` in the app's environment, one new skill. A `sketch` stage's
builder session installs packages and writes src/Main.tsx.

The app's default packages are fixtures only (issue 586): `kit` renders, `sketch` builds and
`fake_publish` posts, and a test's `voice` (the echo fixture renamed, conftest.py) voices, all
answered by a fake ConnectorTransport (kit writes the video and its sidecar; voice writes
timings.json and a silent take per scene; fake_publish previews as its fixture module does, posts
and counts 1,234 views; with SWARMIFY_E2E_PLATFORM set, it uploads resumably to that fake platform
instead, keeping a Publish Record as a real package does); a connector a test adds goes through the
real MCP transport. Every world has the kit Template `shorts` its workflows render. Upkeep runs
every second.

The GraphWriter's extract writes a fixed graph instead of calling graphify's model, a second
after the last change; the real graphify MCP process answers queries over it."""

import importlib.util
import json
import os
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from claude_agent_sdk import ClaudeAgentOptions
from fastapi import FastAPI

from swarmify_api.main import create_app as real_app
from swarmify_api.planner import SYSTEM as PLANNER_SYSTEM
from swarmify_connectors import Connector, ConnectorError, Connectors, FakeTransport, McpTransport
from swarmify_connectors.fake import FIXTURE_DEFAULTS
from swarmify_connectors.mcp_transport import Caller
from swarmify_connectors.model import Tool
from swarmify_learning import SYSTEM as RETRO_SYSTEM
from swarmify_memory import GRAPH_OUT
from swarmify_swarm import SdkRuntime, tool_id
from swarmify_swarm.fake import LAST, Answer, Call, Hold, Say, Script, ScriptedTransport
from swarmify_worlds import World, Worlds

REPO = Path(__file__).parents[2]
FIXTURES = REPO / "packages/connectors/tests/fixtures/packages"
BUILDER_SYSTEM = (FIXTURES / "sketch/session/system.md").read_text()
"""The sketch package's system prompt for its Build Session."""
IDEAS_MD = """# Ideas

```
ffmpeg -i take.mp4 -vf scale=1080:1920,setsar=1 -c:v libx264 -preset slow -crf 18 short.mp4
```

| Hook | Seconds | Views | Retention | Likes | Shares | Comments | Saves |
|---|---|---|---|---|---|---|---|
| Wait for it | 1.0 | 120000 | 64 percent | 5400 | 820 | 311 | 990 |

1. Hooks"""
"""The worker's ideas.md: a wide code block and a wide table for the phone reader, then the list."""
PLANNER = [Answer({"tasks": [{"title": "Pull this week's top shorts"}]})]
WORKER = [
    Say("I'll check what the world already knows, then claim the task."),
    Call(tool_id("query_graph"), {"question": "What do we know already?"}),
    Call(tool_id("claim_task")),
    Call("Bash", {"command": "ls"}),
    Call(tool_id("write_note"), {"title": "Findings", "body": "Hooks in the first second win."}),
    Call(tool_id("write_artifact"), {"name": "ideas.md", "content": IDEAS_MD}),
    Call(tool_id("finish_task"), {"task_id": LAST, "note": "Found two."}),
    Say("Done."),
]
STEER = [
    Say("I'll start once you say which sources to trust."),
    Hold(120),
    *WORKER[1:],
]
"""A worker of a stage named `steer`: mid-turn until a person messages it (or two minutes pass),
so a test can message a live session (issue 534)."""
STEERED = [
    Call(tool_id("query_graph"), {"question": "What does the filing say?"}),
    Call(tool_id("claim_task")),
    Call(tool_id("write_artifact"), {"name": "ideas.md", "content": IDEAS_MD}),
    Call(tool_id("finish_task"), {"task_id": LAST, "note": "Done your way."}),
    Say("Got it. Using the filing, not the blog post."),
]
"""Its reply to a message: the work again, so a message that stopped the turn still finishes."""
IDEAS = [
    {
        "title": "Hook first",
        "body": "Open on the hook.",
        "rank": 1,
        "sources": [],
        "fields": {"hook": "Wait for it", "why": "Hooks in the first second win."},
    },
    {"title": "The chase", "body": "A chase in 30 seconds.", "rank": 2, "sources": []},
]
LINE = {
    "title": "The hedgehog",
    "body": "Who sleeps all winter? A hedgehog.",
    "rank": 1,
    "sources": [],
    "fields": {"text": "Who sleeps all winter? A hedgehog."},
}


def _verdict(options: ClaudeAgentOptions) -> dict[str, Any]:
    """Two ideas, or for a stage promising voice's `line` type, a line in that type's schema
    (issue 133)."""
    if '"text"' in json.dumps(options.output_format):
        return {"passed": True, "reasons": ["A line."], "items": [LINE]}
    return {"passed": True, "reasons": ["Two ideas, ranked."], "items": IDEAS}


JUDGE = [Answer(_verdict)]
BUILDER = [
    Call("Bash", {"command": "npm install"}),
    Call("Write", {"file_path": "src/Main.tsx", "content": "export const Main = () => null;"}),
    Say("A title card over the narration."),
]
RETRO = [Answer({"skill": None, "workflow": None})]
"""The retro after the run proposes nothing, so the run ends without a skill gate."""
HOOKS = "---\nname: hooks\ndescription: Open on the hook.\n---\n\nWrite the hook first.\n"
PROPOSE = [
    Answer(
        {
            "skill": {
                "action": "new",
                "name": "hooks",
                "content": HOOKS,
                "summary": "Open every short on its hook.",
                "evidence": ["research: the judge passed the hook-first idea as #1."],
            },
            "workflow": None,
        }
    )
]

GOLDEN = """name: Weekly short
stages:
  - swarm: research
    goal: Find 5 short-form video ideas trending this week.
    done_when: 5 ideas ranked
    output_type: list[idea]
    produces: [ideas.md]
  - gate: pick
    ask: Pick one idea.
    kind: pick
  - connector: render
    calls: kit
    input: ideas.md
    output: short.mp4
    params: {project: shorts, composition: Short}
  - gate: review
    ask: Watch it. Approve or send back.
    kind: approve
  - gate: confirm
    ask: Post it?
    kind: confirm
  - connector: publish
    calls: fake_publish
    input: short.mp4
    params: {title: Five hooks that work, visibility: unlisted}
"""
LONG = """name: Long haul
stages:
  - swarm: research
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
  - gate: review
    ask: Good enough to go on?
    kind: approve
""" + "".join(f"  - gate: step{i}\n    ask: Go on?\n" for i in range(3, 10))
"""Waiting at step 2 of 9, seven steps not started: the run whose time strip once laid a phone out
444 px wide (issue 175)."""
PLANNER_CHAT = [
    Answer(
        {
            "reply": "Research, you pick, render, review, then post after you confirm.",
            "yaml": GOLDEN,
        }
    )
]
VIDEO = "e2e-video"
SCENES = [("hook", 1.5, True), ("payoff", 2.0, False)]
"""The voice fake's takes: scene, seconds, and whether its first line runs into the next."""


def voice(out: Path) -> dict[str, Any]:
    """What the voice fake leaves: timings.json and a silent take per scene beside it."""
    (out.parent / "narration").mkdir(exist_ok=True)
    takes = []
    for scene, seconds, joined in SCENES:
        with wave.open(str(out.parent / f"narration/{scene}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\0\0" * int(8000 * seconds))
        lines = [{"text": "One.", "joined_next": joined}, {"text": "Two.", "joined_next": False}]
        takes.append(
            {"scene": scene, "file": f"narration/{scene}.wav", "duration": seconds, "lines": lines}
        )
    out.write_text(json.dumps({"engine": "http://localhost:8881", "takes": takes}))
    total = sum(s for _, s, _ in SCENES)
    return {
        "output": str(out),
        "takes": len(takes),
        "seconds": total,
        "message": f"{len(takes)} takes",
    }


PLATFORM = os.environ.get("SWARMIFY_E2E_PLATFORM", "")
"""A fake platform's address: when set, fake_publish uploads to it resumably, which a test serves
outside the app so it outlives a restart."""


def _fake_publish() -> Any:
    """The fake_publish fixture's module: its preview, as its tool would answer."""
    spec = importlib.util.spec_from_file_location(
        "fake_publish", FIXTURES / "fake_publish/fake_publish.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upload(arguments: dict[str, Any]) -> dict[str, Any]:
    """Upload the input to PLATFORM in one resumable session, or finish the session an earlier
    try under the same key began, from its Publish Record in the stage folder (as a real publish
    package does). `unsure:` when it cannot tell."""
    video, key = Path(arguments["input_path"]), arguments.get("idempotency_key", "")
    run = next(p for p in video.parents if p.parent.name == "runs")
    record = run / key.split(".")[0] / f"fake_publish-{key}.json"
    kept = json.loads(record.read_text()) if record.is_file() else {}
    body, size = video.read_bytes(), video.stat().st_size

    def done(answer: httpx.Response) -> dict[str, Any]:
        vid = str(answer.json()["id"])
        return {"id": vid, "url": f"https://fake.example.org/v/{vid}", "status": "published"}

    with httpx.Client(base_url=PLATFORM, timeout=httpx.Timeout(60.0, write=None)) as http:
        if session := kept.get("session"):
            try:
                at = http.put(session, headers={"Content-Range": f"bytes */{size}"})
            except httpx.HTTPError as exc:
                raise ConnectorError("fake_publish: unsure: the platform did not answer") from exc
            if at.status_code == 200:
                return done(at)
            if at.status_code != 308:
                raise ConnectorError(f"fake_publish: unsure: the session answered {at.status_code}")
            start = int(at.headers.get("range", "-1").rpartition("-")[2]) + 1
        else:
            started = http.post("/upload", headers={"X-Upload-Content-Length": str(size)})
            session, start = started.headers["location"], 0
            record.write_text(json.dumps({"session": session}))
        span = {"Content-Range": f"bytes {start}-{size - 1}/{size}"} if start else {}
        try:
            sent = http.put(session, content=body[start:], headers=span)
        except httpx.HTTPError as exc:
            raise ConnectorError("fake_publish: unsure: the upload dropped") from exc
        return done(sent)


FAKED = {
    "kit": ["kit"],
    "sketch": ["finish", "preview"],
    "voice": ["voice"],
    "fake_publish": ["preview", "publish", "outcomes"],
}
"""The fixture packages the transport answers itself, with their tools: none starts a process."""


class Transport(McpTransport):
    """The real MCP transport, but the fixture packages answer from fakes."""

    def __init__(self) -> None:
        super().__init__()
        self.fake = FakeTransport(
            outputs={
                "publish": [{"id": VIDEO, "url": f"https://fake.example.org/v/{VIDEO}"}],
                "outcomes": [{VIDEO: {"views": 1234}}],
            }
        )
        self.preview = _fake_publish().preview_of

    def list_tools(self, connector: Connector, env: dict[str, str]) -> list[Tool]:
        if connector.name in FAKED:
            return [Tool(t) for t in FAKED[connector.name]]
        return super().list_tools(connector, env)

    def call(
        self, connector: Connector, tool: str, arguments: dict[str, Any], env: dict[str, str]
    ) -> dict[str, Any]:
        if connector.name == "kit" and tool == "kit":
            out = Path(arguments["output_path"])
            out.write_bytes(b"not really an mp4")
            head = {**arguments["frontmatter"], "type": "video", "file": out.name}
            front = "\n".join(f"{k}: {json.dumps(v)}" for k, v in head.items())
            out.with_name(f"{out.name}.md").write_text(f"---\n{front}\n---\n\n# {out.name}\n")
            return self.fake.call(connector, tool, arguments, env) | {"output": out.name}
        if connector.name == "fake_publish":
            if tool == "preview":
                return self.preview(arguments)
            if tool == "publish" and PLATFORM:
                return upload(arguments)
            return self.fake.call(connector, tool, arguments, env)
        if connector.name == "voice" and tool == "voice":
            return voice(Path(arguments["output_path"]))
        if connector.name in FAKED:
            return self.fake.call(connector, tool, arguments, env)
        return super().call(connector, tool, arguments, env)

    @contextmanager
    def session(self, connector: Connector, env: dict[str, str]) -> Iterator[Caller]:
        """Each call of a stage's session by itself, so the fakes above answer theirs."""
        yield lambda tool, arguments: self.call(connector, tool, arguments, env)


GRAPH = {
    "directed": False,
    "multigraph": False,
    "graph": {},
    "nodes": [
        {"id": "hooks", "label": "Short hooks", "source_file": "notes/findings.md"},
        {"id": "cliff", "label": "Retention cliff", "source_file": "notes/findings.md"},
    ],
    "links": [{"source": "hooks", "target": "cliff", "relation": "beats"}],
}


def extract(folder: Path, allow_shrink: bool = False) -> None:
    (folder / GRAPH_OUT).mkdir(exist_ok=True)
    (folder / GRAPH_OUT / "graph.json").write_text(json.dumps(GRAPH))


def _with_shorts_template() -> None:
    """Every world made gets the kit Template `shorts` the e2e workflows render (ADR 0028), as
    if its operator had placed it."""
    made = Worlds.create

    def create(self: Worlds, name: str, purpose: str = "") -> World:
        world = made(self, name, purpose)
        (self.folder(world.id) / "assets/kit_templates/shorts").mkdir(parents=True)
        return world

    Worlds.create = create  # type: ignore[method-assign]


def _fixture_defaults() -> None:
    """The defaults are the fixture packages, never the real ones."""
    real = Connectors.install_defaults

    def install_defaults(self: Connectors, sources: Any = None) -> dict[str, str]:
        return real(self, FIXTURE_DEFAULTS if sources is None else sources)

    Connectors.install_defaults = install_defaults  # type: ignore[method-assign]


def create_app() -> FastAPI:
    _fixture_defaults()
    retro = PROPOSE if os.environ.get("SWARMIFY_E2E_RETRO") == "propose" else RETRO
    scripts = {  # the first whose marker is in the system prompt plays the session
        PLANNER_SYSTEM: Script(PLANNER_CHAT, cost=0.0042),
        RETRO_SYSTEM: Script(retro, cost=0.0042),
        BUILDER_SYSTEM: Script(BUILDER, cost=0.0042),
        "You are the planner of a swarm": Script(PLANNER, cost=0.0042),
        "You are the judge of a swarm": Script(JUDGE, cost=0.0042),
        "a worker in a swarm. Stage: steer": Script(STEER, cost=0.0042, replies=STEERED),
        "a worker in a swarm": Script(WORKER, cost=0.0042),
    }

    def transport(options: ClaudeAgentOptions) -> ScriptedTransport:
        prompt = str(options.system_prompt)
        script = next(s for marker, s in scripts.items() if marker in prompt)
        return script.transport(options)

    _with_shorts_template()
    return real_app(
        transport=Transport(),
        runtime=SdkRuntime(transport=transport),
        extract=extract,
        graph_debounce=1.0,
        graph_poll=0.2,
        upkeep_seconds=1.0,  # view counts and retention every second, not every six hours
    )
