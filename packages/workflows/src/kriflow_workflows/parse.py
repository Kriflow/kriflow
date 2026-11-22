"""Workflow YAML to a WorkflowModel. Every schema problem is a named Violation; nothing raises."""

from typing import Any

import yaml

from kriflow_kernel import local_time_problem
from kriflow_workflows.model import (
    GATE_ANSWERS,
    GATE_KINDS,
    MAX_PARALLEL,
    PICKS_MAX,
    STAGE_TYPES,
    ConnectorStage,
    GateStage,
    Stage,
    SwarmStage,
    Violation,
    WorkflowModel,
    WorkflowStage,
)
from kriflow_workflows.schedule import CRON_RULE, cron_problem

TOP_FIELDS = {
    "name", "schedule", "skip_when_live", "budget_usd", "budget_warn_usd", "agents", "given",
    "stages",
}  # fmt: skip
STAGE_FIELDS: dict[str, set[str]] = {
    "swarm": {
        "goal",
        "done_when",
        "tools",
        "graphs",
        "output_type",
        "produces",
        "model",
        "agents",
        "not_before",
        "login",
    },
    "gate": {"ask", "kind", "max_send_backs", "picks", "entry"},
    "connector": {"calls", "input", "inputs", "output", "params", "not_before"},
    "workflow": {
        "runs", "take", "for_each", "as", "max_parallel", "approve", "not_before", "from_list",
        "runs_by",
    },
}  # fmt: skip
LIST_QUERY = {"list", "status", "where", "order", "take", "on_start", "on_finish"}


class _Loader(yaml.SafeLoader):
    """Safe YAML that leaves timestamps as the text written, so `schedule: 2099-01-01T09:00:00Z`
    stays a string a stage config can carry through JSON."""


_Loader.add_constructor("tag:yaml.org,2002:timestamp", lambda loader, node: node.value)


class _Fields:
    """Typed reads from one mapping, recording a violation per bad field instead of raising."""

    def __init__(self, data: dict[str, Any], out: list[Violation], stage: str | None) -> None:
        self.data, self.out, self.stage = data, out, stage

    def fail(self, message: str) -> None:
        self.out.append(Violation("schema", message, self.stage))

    def text(self, key: str, *, required: bool = False) -> str | None:
        value = self.data.get(key)
        if value is None:
            if required:
                self.fail(f"{key} is required")
            return None
        if not isinstance(value, str) or not value.strip():
            self.fail(f"{key} must be a non-empty string")
            return None
        return value.strip()

    def names(self, key: str) -> list[str]:
        value = self.data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            self.fail(f"{key} must be a list of names")
            return []
        return value

    def number(self, key: str, *, integer: bool = False) -> float | int | None:
        value = self.data.get(key)
        if value is None:
            return None
        ok = isinstance(value, int) if integer else isinstance(value, int | float)
        if not ok or isinstance(value, bool) or value <= 0:
            self.fail(f"{key} must be a positive {'whole ' if integer else ''}number")
            return None
        return value

    def unknown(self, allowed: set[str]) -> None:
        for key in sorted(set(self.data) - allowed):
            self.fail(f"unknown field '{key}'")


def parse_workflow(text: str) -> tuple[WorkflowModel | None, list[Violation]]:
    """Return (model, violations). The model is None only when the document has no usable shape."""
    try:
        doc = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        problem = getattr(exc, "problem", None) or "could not parse"
        return None, [Violation("yaml", f"not valid YAML{where}: {problem}")]
    if not isinstance(doc, dict):
        return None, [Violation("schema", "a workflow is a mapping with a name and stages")]

    out: list[Violation] = []
    top = _Fields(doc, out, None)
    name = top.text("name", required=True)
    schedule = top.text("schedule")
    if schedule is not None and (problem := cron_problem(schedule)) is not None:
        out.append(Violation(CRON_RULE, problem))
    skip = doc.get("skip_when_live", False)
    if not isinstance(skip, bool):
        top.fail("skip_when_live must be true or false")
    elif skip and schedule is None:
        top.fail("skip_when_live skips schedule ticks: give a schedule")
    budget = top.number("budget_usd")
    warn = top.number("budget_warn_usd")
    if warn is not None and (budget is None or warn >= budget):
        top.fail("budget_warn_usd must be below budget_usd")
    agents = top.number("agents", integer=True)
    given = top.names("given")
    for bad in [g for g in given if not _plain(g)]:
        top.fail(f"given names files, not paths: '{bad}'")
    top.unknown(TOP_FIELDS)
    raw_stages = doc.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        top.fail("stages must be a non-empty list")
        raw_stages = []

    stages: list[Stage] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_stages, start=1):
        stage_name, stage = _parse_stage(index, raw, out, agents)
        if stage_name in seen:
            out.append(Violation("schema", f"stage name '{stage_name}' is used twice", stage_name))
        if stage_name is not None:
            seen.add(stage_name)
        if stage is not None:
            stages.append(stage)
    if name is None:
        return None, out
    model = WorkflowModel(
        name,
        stages,
        schedule,
        budget,
        int(agents) if agents is not None else None,
        given,
        warn,
        skip is True,
    )
    return model, out


def _parse_stage(
    index: int, raw: Any, out: list[Violation], agents: float | None = None
) -> tuple[str | None, Stage | None]:
    """Return (stage name, stage). The name is known even when the stage's own fields fail.
    `agents` is the workflow's worker count, for a swarm stage that does not set its own."""
    label = f"stage {index}"
    if not isinstance(raw, dict):
        out.append(Violation("schema", f"{label} must be a mapping", None))
        return None, None
    kinds = [t for t in STAGE_TYPES if t in raw]
    if len(kinds) != 1:
        out.append(
            Violation("schema", f"{label} needs exactly one of {', '.join(STAGE_TYPES)}", None)
        )
        return None, None
    kind = kinds[0]
    stage_name = raw[kind]
    if not isinstance(stage_name, str) or not stage_name.strip():
        out.append(Violation("schema", f"{label}: {kind} must name the stage", None))
        return None, None
    stage_name = stage_name.strip()
    f = _Fields(raw, out, stage_name)
    f.unknown(STAGE_FIELDS[kind] | {kind})
    before = len(out)
    not_before = f.text("not_before") if kind != "gate" else None
    if not_before is not None and (problem := local_time_problem(not_before)) is not None:
        f.fail(problem)
    stage: Stage | None = None
    if kind == "swarm":
        goal, done_when = f.text("goal", required=True), f.text("done_when", required=True)
        tools, graphs, produces = f.names("tools"), f.names("graphs"), f.names("produces")
        output_type, model, login = f.text("output_type"), f.text("model"), f.text("login")
        count = f.number("agents", integer=True) or agents or 1
        if goal and done_when:
            stage = SwarmStage(
                stage_name,
                goal,
                done_when,
                tools,
                graphs,
                output_type,
                produces,
                model,
                int(count),
                not_before,
                login,
            )
    elif kind == "gate":
        ask = f.text("ask", required=True)
        gate_kind = f.text("kind") or "approve"
        limit = f.number("max_send_backs", integer=True)
        picks = f.number("picks", integer=True)
        entry = _texts(raw.get("entry", {}), "entry", f)
        if stray := sorted(set(entry) - set(GATE_ANSWERS)):
            f.fail(f"entry names answers {', '.join(GATE_ANSWERS)}, not {', '.join(stray)}")
        elif "send_back" in entry and gate_kind != "approve":
            f.fail("entry.send_back needs an approve gate; only it sends back")
        if gate_kind not in GATE_KINDS:
            f.fail(f"kind must be one of {', '.join(GATE_KINDS)}")
        elif limit is not None and gate_kind != "approve":
            f.fail(f"max_send_backs needs an approve gate; a {gate_kind} gate cannot send back")
        elif picks is not None and gate_kind != "pick":
            f.fail("picks needs a pick gate")
        elif picks is not None and picks > PICKS_MAX:
            f.fail(f"picks must be 1 to {PICKS_MAX}")
        elif ask:
            stage = GateStage(
                stage_name,
                ask,
                gate_kind,
                None if limit is None else int(limit),
                None if picks is None else int(picks),
                entry,
            )
    elif kind == "connector":
        calls = f.text("calls") or stage_name
        input_, output, inputs = f.text("input"), f.text("output"), f.names("inputs")
        params = raw.get("params", {})
        if input_ and inputs:
            f.fail("give input or inputs, not both")
        if not isinstance(params, dict):
            f.fail("params must be a mapping")
        else:
            stage = ConnectorStage(stage_name, calls, input_, output, params, inputs, not_before)
    else:
        runs_by = _runs_by(raw.get("runs_by"), f)
        runs = f.text("runs", required="runs_by" not in raw)
        take = f.names("take")
        for_each, given = f.text("for_each"), f.text("as")
        from_list = _from_list(raw.get("from_list"), f)
        fans = for_each is not None or from_list is not None
        most = f.number("max_parallel", integer=True)
        approve = raw.get("approve", False)
        if runs is not None and runs_by is not None:
            f.fail("give runs or runs_by, not both")
        if runs_by is not None and from_list is None:
            f.fail("runs_by picks a workflow by an Entry's field: give from_list")
        if for_each is not None and from_list is not None:
            f.fail("give for_each or from_list, not both")
        if not fans and (given is not None or most is not None):
            f.fail("as and max_parallel belong to a fan-out stage: give for_each or from_list")
        if not isinstance(approve, bool):
            f.fail("approve must be true or false")
        elif approve and not fans:
            f.fail("approve belongs to a fan-out stage: give for_each or from_list")
        if given is not None and not _plain(given):
            f.fail(f"as names the file each child is given, not a path: '{given}'")
        if most is not None and most > MAX_PARALLEL:
            f.fail(f"max_parallel is 1 to {MAX_PARALLEL}")
        if fans:
            given = given or ("item.json" if (for_each or "").endswith(".json") else "item.md")
            most = most or 2
        if runs or runs_by:
            stage = WorkflowStage(
                stage_name,
                runs or "",
                take,
                for_each,
                given,
                None if most is None else int(most),
                approve is True,
                not_before,
                from_list,
                runs_by,
            )
    return stage_name, (stage if len(out) == before else None)


def _plain(name: str) -> bool:
    """A bare file name, as an input is named: no folder, not `.` or `..`."""
    return "/" not in name and "\\" not in name and name not in (".", "..")


def _texts(value: Any, key: str, f: _Fields) -> dict[str, str]:
    """A mapping of names to non-empty strings, or {} with a violation."""
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in value.items()
    ):
        f.fail(f"{key} must map names to names")
        return {}
    return {k: v.strip() for k, v in value.items()}


def _from_list(value: Any, f: _Fields) -> dict[str, Any] | None:
    """A fan-out stage's list query, every key filled in, or None (with a violation when bad)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        f.fail("from_list must be a mapping with a list")
        return None
    q = _Fields(value, f.out, f.stage)
    before = len(f.out)
    q.unknown(LIST_QUERY)
    name = q.text("list", required=True)
    status = value.get("status", [])
    status = [status] if isinstance(status, str) else status
    if not isinstance(status, list) or not all(isinstance(s, str) and s for s in status):
        q.fail("from_list.status must name a status or a list of them")
    where = value.get("where", {})
    if not isinstance(where, dict) or not all(
        isinstance(v, str | int | float) for v in where.values()
    ):
        q.fail("from_list.where must map Entry fields to a text or number each")
    take = q.number("take", integer=True) or 1
    query = {
        "list": name,
        "status": status,
        "where": where,
        "order": q.text("order") or "-score",
        "take": int(take),
        "on_start": q.text("on_start"),
        "on_finish": q.text("on_finish"),
    }
    return query if len(f.out) == before else None


def _runs_by(value: Any, f: _Fields) -> dict[str, Any] | None:
    """`runs_by`: an Entry `field` and a `map` from its values to workflow ids, or None."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"field", "map"}:
        f.fail("runs_by must give a field and a map from its values to workflows")
        return None
    before = len(f.out)
    field = _Fields(value, f.out, f.stage).text("field", required=True)
    mapping = _texts(value["map"], "runs_by.map", f)
    if not mapping and len(f.out) == before:
        f.fail("runs_by.map must map at least one value to a workflow")
    return {"field": field, "map": mapping} if len(f.out) == before else None
