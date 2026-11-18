"""Fan-out Stages (ADR 0031) at the runner's seam, with a non-video example: translate each
paragraph. A list stage, a child workflow of one fake translate stage per paragraph, a join.
Real DBOS on SQLite; fake pin, take, items and connector behind it."""

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from runs_fakes import FakeNotifier, wait_for
from swarmify_runs import Illegal, Item, OverBudget, Pinned, Run, Runner, SqliteStorage, StageDef
from swarmify_runs.control import RunControl

LIST = StageDef("paragraphs", "connector", {"calls": "split"})
TRANSLATE = StageDef("translate", "connector", {"calls": "translate", "input": "item.md"})
REVIEW = StageDef("review", "gate", {"ask": "Approve?", "kind": "approve"})
STAGE = 1  # the fan-out stage's index in every parent below


def fan(source: str = "paragraphs", most: int = 2, given: str = "item.md") -> StageDef:
    config = {"runs": "wf_t", "for_each": source, "as_": given, "max_parallel": most}
    return StageDef("translate each", "workflow", {**config, "take": ["out.md"]})


class Translate:
    """The child's one stage: reads its given Item and fails while `failing` names its title."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.failing: dict[str, int] = {}
        self.seen: dict[str, list[str]] = {}

    def __call__(self, run: Run, stage_id: str, stage: StageDef) -> None:
        given = self.data_dir / "worlds" / run.world_id / "runs" / run.id / "given"
        title = run.name.split(": ", 1)[1] if ": " in run.name else run.name
        self.seen.setdefault(title, []).extend(p.read_text() for p in sorted(given.iterdir()))
        if self.failing.get(title, 0) > 0:
            self.failing[title] -= 1
            raise RuntimeError(f"could not translate {title}")


class FileTake:
    """Writes each taken name into the parent stage's Item folder, as Memory's copy does."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def __call__(
        self, child: Run, parent: Run, stage_id: str, names: list[str], folder: str = ""
    ) -> None:
        run = self.data_dir / "worlds" / parent.world_id / "runs" / parent.id
        out = run / stage_id / "artifacts" / folder
        out.mkdir(parents=True, exist_ok=True)
        for name in names:
            (out / name).write_text(child.name)


def cards(tmp_path: Path, count: int) -> list[Item]:
    out = []
    for i in range(1, count + 1):
        card = tmp_path / "cards" / f"paragraph-p{i}.md"
        card.parent.mkdir(exist_ok=True)
        card.write_text(f"---\ntype: paragraph\n---\nParagraph number {i}.\n")
        out.append(Item(f"p{i}", f"Paragraph {i}", file=str(card)))
    return out


@pytest.fixture
def translate(runner: Runner, tmp_path: Path) -> Translate:
    fake = Translate(tmp_path)

    def connector(run: Run, stage_id: str, stage: StageDef) -> None:
        if stage.name == "translate":
            fake(run, stage_id, stage)

    runner.connector = connector
    runner.take = FileTake(tmp_path)
    return fake


def library(runner: Runner, child: list[StageDef], **sources: list[Item]) -> None:
    runner.pin = lambda _id, _n=None: Pinned("world_b", "wf_t", 1, "Translate", child)
    runner.items = lambda _run, _index, stage: sources[str(stage.config["for_each"])]


def fetch(runner: RunControl, run_id: str) -> Run:
    run = runner.get(run_id)
    assert run is not None
    return run


def at(runner: RunControl, run_id: str, status: str) -> Run:
    wait_for(lambda: fetch(runner, run_id).status == status, 20)
    return fetch(runner, run_id)


def children(runner: Runner, run_id: str) -> list[Run]:
    return [fetch(runner, c) for c in runner.storage.child_run_ids(run_id)]


def live(runner: Runner, run_id: str) -> list[Run]:
    return [c for c in children(runner, run_id) if c.status not in ("done", "failed", "aborted")]


def approve(runner: Runner, run_id: str) -> None:
    """Answer the gate of every child waiting at one; a gate answered a moment ago is left."""
    for child in live(runner, run_id):
        if child.status == "waiting_gate":
            with contextlib.suppress(Illegal):
                runner.decide(child.id)


def joined(tmp_path: Path, run: Run) -> list[dict]:
    folder = tmp_path / "worlds" / run.world_id / "runs" / run.id / run.stages[STAGE].id
    return json.loads((folder / "artifacts" / "items.json").read_text())


def test_cards_fan_out_at_most_max_parallel_at_once_and_join_into_a_folder_each(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE, REVIEW], paragraphs=cards(tmp_path, 4))
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan()])
    most = 0
    while fetch(runner, parent.id).status != "done":
        running = live(runner, parent.id)
        most = max(most, len(running))
        assert len(running) <= 2
        approve(runner, parent.id)
        wait_for(lambda: True, 0.05)
    assert most == 2
    done = fetch(runner, parent.id)
    assert [(i.key, i.title, i.status) for i in done.stages[STAGE].items] == [
        (f"p{i}", f"Paragraph {i}", "done") for i in range(1, 5)
    ]
    names = sorted(c.name for c in children(runner, parent.id))
    assert names == [f"Translate: Paragraph {i}" for i in range(1, 5)]
    assert translate.seen["Paragraph 3"] == ["---\ntype: paragraph\n---\nParagraph number 3.\n"]
    index = joined(tmp_path, done)
    assert [(i["key"], i["status"], i["files"]) for i in index] == [
        (f"p{i}", "done", [f"p{i}/out.md"]) for i in range(1, 5)
    ]
    links = {i.key: i.child.id for i in done.stages[STAGE].items if i.child}
    assert [i["child_run_id"] for i in index] == [links[f"p{i}"] for i in range(1, 5)]


def test_a_json_list_gives_each_child_its_element_as_item_json(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    elements = [{"id": "intro", "title": "Intro", "text": "Hello"}, {"text": "Two"}, {"text": "3"}]
    items = [
        Item("intro", "Intro", data=elements[0]),
        Item("1", "1", data=elements[1]),
        Item("2", "2", data=elements[2]),
    ]
    library(runner, [TRANSLATE], **{"paragraphs.json": items})
    parent = runner.start(
        "world_a", "wf_p", 1, "Book", [LIST, fan("paragraphs.json", 3, "item.json")]
    )
    done = at(runner, parent.id, "done")
    for element, title in zip(elements, ("Intro", "1", "2"), strict=True):
        assert [json.loads(t) for t in translate.seen[title]] == [element]
    assert [i["key"] for i in joined(tmp_path, done)] == ["intro", "1", "2"]


def test_a_failed_item_never_stops_its_siblings_and_its_gate_retries_only_it(
    runner: Runner, translate: Translate, tmp_path: Path, notifier: FakeNotifier
) -> None:
    library(runner, [TRANSLATE], paragraphs=cards(tmp_path, 3))
    translate.failing["Paragraph 2"] = 1
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan()])
    waiting = at(runner, parent.id, "waiting_gate")
    assert [i.status for i in waiting.stages[STAGE].items] == ["done", "failed", "done"]
    (gate,) = runner.gates()
    assert (gate.run_id, gate.kind, gate.stage_name) == (parent.id, "items", "translate each")
    assert "Paragraph 2 (p2): RuntimeError: could not translate Paragraph 2" in gate.ask
    # The notice is sent after the gate is stored, so it may land a moment later.
    wait_for(lambda: any(e.type == "gate.opened" for e in notifier.events))
    opened = [e for e in notifier.events if e.type == "gate.opened"]
    assert opened[-1].payload["kind"] == "items"
    with pytest.raises(ValueError, match="retry or continue"):
        runner.decide(parent.id, choice="maybe")

    runner.decide(parent.id, choice="retry")
    done = at(runner, parent.id, "done")
    assert [i.status for i in done.stages[STAGE].items] == ["done", "done", "done"]
    assert {t: len(s) for t, s in translate.seen.items()} == {
        "Paragraph 1": 1, "Paragraph 2": 2, "Paragraph 3": 1,
    }  # fmt: skip
    assert [i["status"] for i in joined(tmp_path, done)] == ["done"] * 3


def test_continue_finishes_without_the_failed_item_marked_failed(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE], paragraphs=cards(tmp_path, 2))
    translate.failing["Paragraph 1"] = 9
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan(), REVIEW])
    at(runner, parent.id, "waiting_gate")
    runner.decide(parent.id, choice="continue")
    wait_for(lambda: [g.stage_name for g in runner.gates()] == ["review"])
    index = joined(tmp_path, fetch(runner, parent.id))
    assert [(i["key"], i["status"], i["files"]) for i in index] == [
        ("p1", "failed", []),
        ("p2", "done", ["p2/out.md"]),
    ]


def test_zero_items_finish_with_an_empty_index(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE], paragraphs=[])
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan()])
    assert joined(tmp_path, at(runner, parent.id, "done")) == []
    assert children(runner, parent.id) == []


def test_abort_cascades_to_every_live_child_and_starts_no_more(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE, REVIEW], paragraphs=cards(tmp_path, 3))
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan()])
    wait_for(lambda: sum(c.status == "waiting_gate" for c in children(runner, parent.id)) == 2)
    runner.abort(parent.id)
    at(runner, parent.id, "aborted")
    wait_for(lambda: all(c.status == "aborted" for c in children(runner, parent.id)))
    assert len(children(runner, parent.id)) == 2


def test_pause_holds_new_children_and_pauses_live_ones_until_resume(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE, REVIEW], paragraphs=cards(tmp_path, 3))
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan()])
    wait_for(lambda: sum(c.status == "waiting_gate" for c in children(runner, parent.id)) == 2)
    runner.pause(parent.id)
    wait_for(lambda: all(c.status == "paused" for c in children(runner, parent.id)))
    first, second = children(runner, parent.id)
    runner.resume(first.id)
    at(runner, first.id, "waiting_gate")
    runner.decide(first.id)
    at(runner, first.id, "done")
    wait_for(lambda: fetch(runner, parent.id).stages[STAGE].items[0].status == "done")
    assert len(children(runner, parent.id)) == 2  # paused: the third Item waits

    runner.resume(parent.id)
    at(runner, second.id, "waiting_gate")
    wait_for(lambda: len(children(runner, parent.id)) == 3)
    for child in children(runner, parent.id):
        if child.id != first.id:
            at(runner, child.id, "waiting_gate")
            runner.decide(child.id)
    at(runner, parent.id, "done")


def test_a_restart_mid_fan_out_starts_no_item_twice(
    tmp_path: Path, storage: SqliteStorage, translate: Translate, runner: Runner
) -> None:
    runner.shutdown()
    items = cards(tmp_path, 3)

    def launched() -> Runner:
        r = Runner(storage, tmp_path / "dbos.sqlite", runner.connector, tmp_path, poll_seconds=0.05)
        library(r, [TRANSLATE, REVIEW], paragraphs=items)
        r.take = FileTake(tmp_path)
        r.launch()
        return r

    first = launched()
    parent = first.start("world_a", "wf_p", 1, "Book", [LIST, fan()])
    wait_for(lambda: sum(c.status == "waiting_gate" for c in children(first, parent.id)) == 2)
    first.shutdown()

    second = launched()
    try:
        while fetch(second, parent.id).status != "done":
            approve(second, parent.id)
            wait_for(lambda: True, 0.05)
        assert len(children(second, parent.id)) == 3
        assert sorted(translate.seen) == ["Paragraph 1", "Paragraph 2", "Paragraph 3"]
    finally:
        second.shutdown()
    runner.launch()  # the fixture shuts it down again


def test_a_send_back_naming_one_item_reruns_only_it_and_naming_none_reruns_all(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE], paragraphs=cards(tmp_path, 3))
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan(), REVIEW])
    at(runner, parent.id, "waiting_gate")
    runner.decide(parent.id, send_back_to="translate each", note="Redo paragraph 2, too stiff")
    wait_for(lambda: fetch(runner, parent.id).send_backs == 1)
    again = at(runner, parent.id, "waiting_gate")
    assert {t: len(s) for t, s in translate.seen.items()} == {
        "Paragraph 1": 1, "Paragraph 2": 2, "Paragraph 3": 1,
    }  # fmt: skip
    assert [i["files"] for i in joined(tmp_path, again)] == [
        ["p1/out.md"],
        ["p2/out.md"],
        ["p3/out.md"],
    ]
    assert len(children(runner, parent.id)) == 4

    runner.decide(parent.id, send_back_to="translate each", note="All of it, more casual")
    wait_for(lambda: fetch(runner, parent.id).send_backs == 2)
    at(runner, parent.id, "waiting_gate")
    assert {t: len(s) for t, s in translate.seen.items()} == {
        "Paragraph 1": 2, "Paragraph 2": 3, "Paragraph 3": 2,
    }  # fmt: skip


def test_a_send_back_reruns_only_the_items_its_note_does_not_say_to_keep(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE], paragraphs=cards(tmp_path, 3))
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan(), REVIEW])
    at(runner, parent.id, "waiting_gate")
    note = (
        "Paragraph 2: too stiff, redo it. Paragraph 1 and paragraph 3 pass as they are; keep them."
    )
    runner.decide(parent.id, send_back_to="translate each", note=note)
    wait_for(lambda: fetch(runner, parent.id).send_backs == 1)
    at(runner, parent.id, "waiting_gate")
    assert {t: len(s) for t, s in translate.seen.items()} == {
        "Paragraph 1": 1, "Paragraph 2": 2, "Paragraph 3": 1,
    }  # fmt: skip



def test_a_sentence_asking_for_a_redo_reruns_its_item_even_with_a_keep_word(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE], paragraphs=cards(tmp_path, 3))
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan(), REVIEW])
    at(runner, parent.id, "waiting_gate")
    note = (
        "Paragraph 2: the remake came back unchanged and still reads stiff."
        " Paragraph 1 and paragraph 3 pass as they are; keep them."
    )
    runner.decide(parent.id, send_back_to="translate each", note=note)
    wait_for(lambda: fetch(runner, parent.id).send_backs == 1)
    at(runner, parent.id, "waiting_gate")
    assert {t: len(s) for t, s in translate.seen.items()} == {
        "Paragraph 1": 1, "Paragraph 2": 2, "Paragraph 3": 1,
    }  # fmt: skip

class Spender:
    """A child's one swarm stage: spends $1 per call for `calls` calls and asks the Budget
    after each, as the worker's cost hook does."""

    def __init__(self, calls: int) -> None:
        self.calls, self.cost, self.done = calls, {}, {}
        self.control: RunControl | None = None

    def spend(self, run_ids: list[str]) -> float:
        return sum(self.cost.get(r, 0.0) for r in run_ids)

    def __call__(self, run: Run, stage_id: str, stage: StageDef) -> None:
        assert self.control is not None
        while self.done.get(stage_id, 0) < self.calls:
            if self.control.check_stop(run.id):
                raise OverBudget()
            self.done[stage_id] = self.done.get(stage_id, 0) + 1
            self.cost[run.id] = self.cost.get(run.id, 0.0) + 1.0


@pytest.fixture
def spender(tmp_path: Path, storage: SqliteStorage, notifier: FakeNotifier) -> Iterator[Runner]:
    swarm = Spender(calls=3)
    r = Runner(
        storage, tmp_path / "dbos.sqlite", lambda *_: None, tmp_path, notifier, swarm=swarm,
        poll_seconds=0.05, spend=swarm.spend,
    )  # fmt: skip
    swarm.control = r
    r.take = FileTake(tmp_path)
    r.launch()
    yield r
    r.shutdown()


def test_a_spent_budget_waits_at_one_gate_on_the_top_run_and_money_releases_every_child(
    spender: Runner, tmp_path: Path, notifier: FakeNotifier
) -> None:
    library(spender, [StageDef("translate", "swarm", {"goal": "g"})], paragraphs=cards(tmp_path, 3))
    parent = spender.start("world_a", "wf_p", 1, "Book", [LIST, fan()], budget_usd=4)
    wait_for(lambda: sum(c.over_budget for c in children(spender, parent.id)) == 2)
    (gate,) = spender.gates()
    assert (gate.kind, gate.run_id, gate.stage_name) == ("budget", parent.id, "translate each")
    wait_for(lambda: any(e.type == "gate.opened" for e in notifier.events))
    opened = [e for e in notifier.events if e.type == "gate.opened"]
    assert opened and {e.run_id for e in opened} == {parent.id}

    spender.set_budget(parent.id, 100)
    done = at(spender, parent.id, "done")
    assert [i.status for i in done.stages[STAGE].items] == ["done"] * 3
    closed = [e for e in notifier.events if e.type == "gate.closed"]
    assert [e.run_id for e in closed] == [parent.id]


def test_a_failed_item_is_retried_now_from_where_its_child_failed_while_siblings_wait(
    runner: Runner, translate: Translate, tmp_path: Path
) -> None:
    library(runner, [TRANSLATE, REVIEW], paragraphs=cards(tmp_path, 3))
    translate.failing["Paragraph 2"] = 1
    parent = runner.start("world_a", "wf_p", 1, "Book", [LIST, fan(most=3)])
    wait_for(lambda: [i.status for i in fetch(runner, parent.id).stages[STAGE].items]
             == ["running", "failed", "running"], 20)  # fmt: skip
    with pytest.raises(Illegal, match="only a failed one"):
        runner.retry_item(parent.id, "p1")
    failed = next(i.child for i in fetch(runner, parent.id).stages[STAGE].items if i.key == "p2")
    assert failed is not None

    child = runner.retry_item(parent.id, "p2")
    assert child.parent_run_id == parent.id and child.forked_from is not None
    assert child.forked_from.id == failed.id and child.budget_usd is None
    while fetch(runner, parent.id).status != "done":
        approve(runner, parent.id)
        wait_for(lambda: True, 0.05)
    done = fetch(runner, parent.id)
    assert [(i.key, i.status) for i in done.stages[STAGE].items] == [
        ("p1", "done"), ("p2", "done"), ("p3", "done"),
    ]  # fmt: skip
    retried = next(i.child for i in done.stages[STAGE].items if i.key == "p2")
    assert retried is not None and retried.id == child.id
    assert len(translate.seen["Paragraph 2"]) == 2
