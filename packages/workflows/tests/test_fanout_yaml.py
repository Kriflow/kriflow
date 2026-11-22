"""Fan-out Stages (ADR 0031) in the language: parse, dump and the rules a save checks."""

from dataclasses import asdict

import pytest

from kriflow_workflows import (
    ConnectorStage,
    GateStage,
    SwarmStage,
    WorkflowModel,
    WorkflowStage,
)
from kriflow_workflows.dump import dump_workflow
from kriflow_workflows.parse import parse_workflow
from kriflow_workflows.validate import validate
from samples import CONNECTORS

LIST = "  - swarm: paragraphs\n    goal: g\n    done_when: d\n    produces: [paragraphs.json]\n"


def parsed(fan: str, top: str = "") -> WorkflowModel:
    model, violations = parse_workflow(f"name: N\n{top}stages:\n{LIST}{fan}")
    assert violations == [] and model is not None
    return model


def schema(fan: str) -> list[str]:
    return [v.message for v in parse_workflow(f"name: N\nstages:\n{LIST}{fan}")[1]]


def test_a_fan_out_stage_parses_with_defaults_and_a_plain_one_is_unchanged() -> None:
    fan = "  - workflow: each\n    runs: wf_t\n    for_each: paragraphs\n    take: [out.md]\n"
    assert parsed(fan).stages[1] == WorkflowStage(
        "each", "wf_t", ["out.md"], "paragraphs", "item.md", 2
    )
    json = (
        "  - workflow: each\n    runs: wf_t\n    for_each: paragraphs.json\n    max_parallel: 8\n"
    )
    assert parsed(json).stages[1] == WorkflowStage(
        "each", "wf_t", [], "paragraphs.json", "item.json", 8
    )
    named = "  - workflow: each\n    runs: wf_t\n    for_each: paragraphs\n    as: para.md\n"
    assert parsed(named).stages[1] == WorkflowStage("each", "wf_t", [], "paragraphs", "para.md", 2)
    plain = "  - workflow: each\n    runs: wf_t\n"
    assert parsed(plain).stages[1] == WorkflowStage("each", "wf_t")


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ("    for_each: paragraphs\n    max_parallel: 0\n", "max_parallel must be a positive"),
        ("    for_each: paragraphs\n    max_parallel: 9\n", "max_parallel is 1 to 8"),
        ("    for_each: paragraphs\n    as: a/b.md\n", "as names the file each child is given"),
        ("    max_parallel: 2\n", "as and max_parallel belong to a fan-out stage"),
    ],
)
def test_the_schema_refuses_bad_fan_out_fields(fields: str, message: str) -> None:
    (found,) = schema(f"  - workflow: each\n    runs: wf_t\n{fields}")
    assert found.startswith(message)


def rules(model: WorkflowModel) -> list[tuple[str, str | None]]:
    found = validate(model, workflow_id=None, connectors=CONNECTORS, references={"wf_t": []})
    return [(v.rule, v.stage) for v in found]


def test_for_each_names_an_earlier_stage_or_a_json_file_an_earlier_stage_makes() -> None:
    made = SwarmStage("paragraphs", "g", "d", produces=["paragraphs.json", "notes.md"])
    fan = WorkflowStage("each", "wf_t", for_each="paragraphs", as_="item.md", max_parallel=2)
    assert rules(WorkflowModel("w", [made, fan])) == []
    assert (
        rules(WorkflowModel("w", [made, WorkflowStage("each", "wf_t", [], "paragraphs.json")]))
        == []
    )
    for source in ("later", "unknown", "each", "missing.json", "notes.md"):
        late = SwarmStage("later", "g", "d")
        stage = WorkflowStage("each", "wf_t", [], source, "item.md", 2)
        assert rules(WorkflowModel("w", [made, stage, late])) == [("for_each_unknown", "each")]


def test_a_given_file_counts_as_made_before_the_first_stage() -> None:
    model = parsed(
        "  - connector: read\n    calls: remotion_render\n    input: item.json\n",
        "given: [item.json]\n",
    )
    assert model.given == ["item.json"]
    assert rules(model) == []
    bare = WorkflowModel("w", [ConnectorStage("read", "remotion_render", input="item.json")])
    assert rules(bare) == [("input_not_produced", "read")]
    assert parse_workflow("name: N\ngiven: [a/b.json]\nstages:\n  - gate: g\n    ask: a\n")[1]


def test_dump_writes_as_and_given_back_so_a_fan_out_round_trips() -> None:
    model = parsed(
        "  - workflow: each\n    runs: wf_t\n    for_each: paragraphs\n    as: p.md\n",
        "given: [x.json]\n",
    )
    stages = []
    for stage in model.stages:
        fields = asdict(stage)
        stages.append((fields.pop("type"), fields.pop("name"), fields))
    text = dump_workflow(model.name, stages, given=model.given)
    assert "as: p.md" in text and "as_" not in text and "given:\n- x.json" in text
    assert parse_workflow(text) == (model, [])


def test_a_later_stage_may_read_the_items_json_a_fan_out_stage_writes() -> None:
    made = SwarmStage("paragraphs", "g", "d", produces=["paragraphs.json"])
    fan = WorkflowStage("each", "wf_t", for_each="paragraphs.json", as_="item.json")
    read = ConnectorStage("read", "remotion_render", input="items.json")
    assert rules(WorkflowModel("w", [made, fan, read])) == []
    plain = WorkflowStage("each", "wf_t")
    assert rules(WorkflowModel("w", [made, plain, read])) == [("input_not_produced", "read")]


def test_a_fan_out_stage_may_end_with_an_approve_items_gate_and_it_round_trips() -> None:
    fan = "  - workflow: each\n    runs: wf_t\n    for_each: paragraphs\n    approve: true\n"
    model = parsed(fan)
    assert model.stages[1] == WorkflowStage(
        "each", "wf_t", [], "paragraphs", "item.md", 2, approve=True
    )
    stages = []
    for stage in model.stages:
        fields = asdict(stage)
        stages.append((fields.pop("type"), fields.pop("name"), fields))
    assert "approve: true" in dump_workflow(model.name, stages)
    assert parse_workflow(dump_workflow(model.name, stages)) == (model, [])
    (plain,) = schema("  - workflow: each\n    runs: wf_t\n    approve: true\n")
    assert plain.startswith("approve belongs to a fan-out stage")
    (bad,) = schema(
        "  - workflow: each\n    runs: wf_t\n    for_each: paragraphs\n    approve: 1\n"
    )
    assert bad.startswith("approve must be true or false")


LISTED = """\
  - workflow: make
    from_list: {list: tickets, status: new, where: {kind: urgent}, order: rank, on_start: working}
    runs_by: {field: queue, map: {email: wf_t, chat: wf_b}}
    take: [reply.md]
"""


def test_a_fan_out_stage_may_take_its_items_from_a_list_query() -> None:
    """Issue 541: `from_list` with its defaults filled in, `runs_by` in place of `runs`."""
    stage = parsed(LISTED).stages[1]
    assert stage == WorkflowStage(
        "make", "", ["reply.md"], None, "item.md", 2,
        from_list={
            "list": "tickets", "status": ["new"], "where": {"kind": "urgent"}, "order": "rank",
            "take": 1, "on_start": "working", "on_finish": None,
        },
        runs_by={"field": "queue", "map": {"email": "wf_t", "chat": "wf_b"}},
    )  # fmt: skip
    stages = []
    for each in parsed(LISTED).stages:
        fields = asdict(each)
        stages.append((fields.pop("type"), fields.pop("name"), fields))
    assert parse_workflow(dump_workflow("N", stages)) == (parsed(LISTED), [])
    found = validate(parsed(LISTED), workflow_id=None, connectors=CONNECTORS, references={})
    assert sorted(v.message for v in found) == [
        "no workflow with id 'wf_b'",
        "no workflow with id 'wf_t'",
    ]
    read = ConnectorStage("read", "remotion_render", input="items.json")
    assert rules(WorkflowModel("w", [*parsed(LISTED).stages, read])) == [
        ("unknown_workflow", "make")
    ]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ("    runs: wf_t\n    from_list: tickets\n", "from_list must be a mapping"),
        ("    runs: wf_t\n    from_list: {status: new}\n", "list is required"),
        ("    runs: wf_t\n    from_list: {list: a, limit: 1}\n", "unknown field 'limit'"),
        ("    runs: wf_t\n    from_list: {list: a, take: 0}\n", "take must be a positive"),
        ("    runs: wf_t\n    from_list: {list: a, where: {x: [1]}}\n", "from_list.where"),
        ("    runs_by: {field: f, map: {a: wf_t}}\n", "runs_by picks a workflow"),
        ("    runs_by: {field: f}\n", "runs_by must give a field and a map"),
        (
            "    runs: wf_t\n    runs_by: {field: f, map: {a: wf_t}}\n    from_list: {list: a}\n",
            "give runs or runs_by",
        ),
        ("    runs: wf_t\n    for_each: paragraphs\n    from_list: {list: a}\n", "give for_each"),
        ("    from_list: {list: a}\n", "runs is required"),
    ],
)
def test_the_schema_refuses_a_bad_list_query(fields: str, message: str) -> None:
    (found,) = schema(f"  - workflow: each\n{fields}")
    assert found.startswith(message), found


def test_a_gate_names_where_its_runs_entry_moves_per_answer() -> None:
    gate = "  - gate: review\n    ask: Good?\n    entry: {approve: review, send_back: working}\n"
    (_, stage) = parsed(gate).stages
    assert isinstance(stage, GateStage)
    assert stage.entry == {"approve": "review", "send_back": "working"}
    (stray,) = schema("  - gate: g\n    ask: a\n    entry: {publish: x}\n")
    assert stray.startswith("entry names answers approve, send_back, reject")
    (pick,) = schema("  - gate: g\n    ask: a\n    kind: pick\n    entry: {send_back: x}\n")
    assert pick.startswith("entry.send_back needs an approve gate")
