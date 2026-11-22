"""Fan-out from a World List query (issue 541) at the runner's seam: the best Entry is claimed
and handed to its child, overlapping runs never share one, an ended run frees what it held, an
empty query starts nothing, `runs_by` picks each child's workflow, and a person's gate answer
moves the Entry. A non-video example: a list of support tickets, each worked by a child run.
Real DBOS on SQLite; fake pin and connector behind it."""

import threading
from pathlib import Path

import pytest

from runs_fakes import FakeConnector, wait_for
from swarmify_runs import Pinned, Run, Runner, SqliteStorage, StageDef, WorldLists

WORLD = "world_a"
STATUSES = ["open", "working", "review", "closed", "rejected"]
WORK = StageDef("work", "connector", {"calls": "reply"})
BREAK = StageDef("explode", "connector", {"calls": "reply"})  # FakeConnector raises on it
MOVES = {"approve": "review", "send_back": "working", "reject": "rejected"}
CHECK = StageDef("check", "gate", {"ask": "Good reply?", "kind": "approve", "entry": MOVES})


def fan(take: int = 1, runs_by: dict | None = None, **query: object) -> StageDef:
    from_list = {"list": "tickets", "status": ["open"], "order": "rank", "take": take, **query}
    config = {"from_list": from_list, "max_parallel": 4, "as_": "item.md"}
    config |= {"runs_by": runs_by} if runs_by else {"runs": "wf_reply"}
    return StageDef("each ticket", "workflow", config)


@pytest.fixture
def lists(runner: Runner, storage: SqliteStorage, tmp_path: Path) -> WorldLists:
    lists = WorldLists(storage.path)
    runner.lists = lists
    lists.declare(WORLD, "tickets", STATUSES, done=["closed"], rejected=["rejected"])
    folder = tmp_path / "worlds" / WORLD / "tickets"
    folder.mkdir(parents=True)
    for key, rank, kind in (("t-a", 3, "billing"), ("t-b", 1, "login"), ("t-c", 2, "billing")):
        (folder / f"{key}.md").write_text(f"Ticket {key}\n")
        fields = {"rank": rank, "kind": kind}
        lists.add(WORLD, "tickets", key, f"Ticket {key}", fields=fields, file=f"tickets/{key}.md")
    return lists


def library(runner: Runner, **workflows: list[StageDef]) -> None:
    def pin(workflow_id: str, _version: int | None = None) -> Pinned:
        return Pinned(
            WORLD, workflow_id, 1, workflow_id.removeprefix("wf_"), workflows[workflow_id]
        )

    runner.pin = pin


def fetch(runner: Runner, run_id: str) -> Run:
    run = runner.get(run_id)
    assert run is not None
    return run


def at(runner: Runner, run_id: str, status: str) -> Run:
    wait_for(lambda: fetch(runner, run_id).status == status, 20)
    return fetch(runner, run_id)


def kids(runner: Runner, run_id: str) -> list[Run]:
    return [fetch(runner, c) for c in runner.storage.child_run_ids(run_id)]


def given(tmp_path: Path, child: Run) -> str:
    return (tmp_path / "worlds" / WORLD / "runs" / child.id / "given" / "item.md").read_text()


def test_the_best_entry_by_rank_is_claimed_handed_to_its_child_and_moved_on(
    runner: Runner, lists: WorldLists, tmp_path: Path
) -> None:
    library(runner, wf_reply=[WORK, CHECK])
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(on_start="working", on_finish="closed")])
    wait_for(lambda: [c.status for c in kids(runner, parent.id)] == ["waiting_gate"])
    (child,) = kids(runner, parent.id)
    entry = lists.get(WORLD, "tickets", "t-b")  # rank 1, the lowest
    assert (entry.status, entry.claimed_by, entry.runs) == (
        "working",
        child.id,
        [parent.id, child.id],
    )
    assert given(tmp_path, child) == "Ticket t-b\n"
    assert child.name == "reply: Ticket t-b"
    assert [e.claimed_by for e in (lists.get(WORLD, "tickets", k) for k in ("t-a", "t-c"))] == [
        None,
        None,
    ]

    runner.decide(child.id, person=True)
    at(runner, parent.id, "done")
    entry = lists.get(WORLD, "tickets", "t-b")
    assert (entry.status, entry.claimed_by) == ("closed", None)  # on_finish after the gate's
    moves = [
        (e.payload.get("from"), e.payload.get("to"), e.payload["by"])
        for e in lists.history(WORLD, "tickets", "t-b")
        if "to" in e.payload
    ]
    assert moves == [("open", "working", "run"), ("working", "review", "operator"),
                     ("review", "closed", "run")]  # fmt: skip


def test_order_by_a_field_descending_and_a_field_filter_pick_the_entry(
    runner: Runner, lists: WorldLists
) -> None:
    library(runner, wf_reply=[WORK, CHECK])
    stage = fan(order="-rank", where={"kind": "billing"})
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [stage])
    wait_for(lambda: [c.status for c in kids(runner, parent.id)] == ["waiting_gate"])
    (child,) = kids(runner, parent.id)
    assert lists.get(WORLD, "tickets", "t-a").claimed_by == child.id  # billing, rank 3


def test_two_overlapping_runs_never_claim_the_same_entry(runner: Runner, lists: WorldLists) -> None:
    library(runner, wf_reply=[WORK, CHECK])
    first = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(take=2)])
    second = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(take=2)])

    def handed() -> bool:  # a child's row lands before its Entry is handed to it
        held = [lists.get(WORLD, "tickets", k).claimed_by or "" for k in ("t-a", "t-b", "t-c")]
        owners = {c.id: p for p in (first.id, second.id) for c in kids(runner, p)}
        return len(owners) == 3 and sorted(held) == sorted(owners)

    wait_for(handed)  # every Entry with one child, none twice


def test_claims_are_atomic_under_threads(lists: WorldLists) -> None:
    for n in range(20):
        lists.add(WORLD, "tickets", f"x{n}", f"X {n}", fields={"rank": 10 + n})
    got: list[str] = []

    def claim(run_id: str) -> None:
        got.extend(e.key for e in lists.claim(WORLD, "tickets", run_id, order="rank", take=2))

    threads = [threading.Thread(target=claim, args=(f"run_{n}",)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 20 and len(set(got)) == 20
    again = lists.claim(WORLD, "tickets", "run_3", order="rank", take=2)
    assert {e.claimed_by for e in again} == {"run_3"}  # a claim made again takes the same ones


def test_a_failed_child_frees_its_entry_and_an_aborted_family_frees_every_one(
    runner: Runner, lists: WorldLists
) -> None:
    library(runner, wf_reply=[BREAK], wf_slow=[WORK, CHECK])
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(on_start="working")])
    waiting = at(runner, parent.id, "waiting_gate")  # its items gate: the Item failed
    assert [i.status for i in waiting.stages[0].items] == ["failed"]
    entry = lists.get(WORLD, "tickets", "t-b")
    assert (entry.status, entry.claimed_by) == ("working", None)

    by = {"field": "kind", "map": {"billing": "wf_slow", "login": "wf_slow"}}
    again = fan(take=3, runs_by=by, status=["open", "working"])  # t-b went working above
    aborted = runner.start(WORLD, "wf_p", 1, "Inbox", [again])
    wait_for(lambda: sum(c.status == "waiting_gate" for c in kids(runner, aborted.id)) == 3)
    assert {lists.get(WORLD, "tickets", k).claimed_by for k in ("t-a", "t-b", "t-c")} == {
        c.id for c in kids(runner, aborted.id)
    }
    runner.abort(aborted.id)
    at(runner, aborted.id, "aborted")
    wait_for(lambda: all(c.status == "aborted" for c in kids(runner, aborted.id)))
    assert [lists.get(WORLD, "tickets", k).claimed_by for k in ("t-a", "t-b", "t-c")] == [None] * 3


def test_an_empty_query_ends_the_run_at_once_with_no_child_and_no_call(
    runner: Runner, lists: WorldLists, connector: FakeConnector
) -> None:
    library(runner, wf_reply=[WORK])
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(status=["review"])])
    done = at(runner, parent.id, "done")
    assert done.stages[0].items == [] and kids(runner, parent.id) == []
    assert connector.calls == []


def test_runs_by_starts_each_entrys_mapped_workflow_and_fails_an_unmapped_one(
    runner: Runner, lists: WorldLists
) -> None:
    lists.add(WORLD, "tickets", "t-d", "Ticket t-d", fields={"rank": 0, "kind": "refund"})
    library(runner, wf_money=[WORK], wf_access=[WORK])
    by = {"field": "kind", "map": {"billing": "wf_money", "login": "wf_access"}}
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(take=4, runs_by=by)])
    waiting = at(runner, parent.id, "waiting_gate")
    items = {i.key: (i.status, i.error) for i in waiting.stages[0].items}
    assert items["t-d"] == (
        "failed",
        "Entry 't-d' has kind 'refund', which runs_by maps to no workflow (it maps billing, login)",
    )
    assert sorted(c.name for c in kids(runner, parent.id)) == [
        "access: Ticket t-b", "money: Ticket t-a", "money: Ticket t-c",
    ]  # fmt: skip
    assert lists.get(WORLD, "tickets", "t-d").claimed_by == parent.id  # held for a retry
    runner.decide(parent.id, choice="continue")
    at(runner, parent.id, "done")
    assert lists.get(WORLD, "tickets", "t-d").claimed_by is None


def test_a_person_answer_moves_the_entry_an_agent_answer_does_not(
    runner: Runner, lists: WorldLists
) -> None:
    library(runner, wf_reply=[WORK, CHECK, StageDef("send", "connector", {"calls": "reply"})])
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(take=3)])
    wait_for(lambda: sum(c.status == "waiting_gate" for c in kids(runner, parent.id)) == 3)
    held = {lists.get(WORLD, "tickets", k).claimed_by: k for k in ("t-a", "t-b", "t-c")}
    agent, sent, rejected = kids(runner, parent.id)

    runner.decide(agent.id)  # an agent through MCP: no person
    at(runner, agent.id, "done")
    assert lists.get(WORLD, "tickets", held[agent.id]).status == "open"

    runner.decide(sent.id, send_back_to="work", note="Kinder", person=True)
    assert lists.get(WORLD, "tickets", held[sent.id]).status == "working"

    runner.abort(rejected.id, person=True)
    at(runner, rejected.id, "aborted")
    entry = lists.get(WORLD, "tickets", held[rejected.id])
    assert (entry.status, entry.claimed_by) == ("rejected", None)


def test_a_gate_naming_a_status_the_list_lacks_refuses_the_answer(
    runner: Runner, lists: WorldLists
) -> None:
    wrong = StageDef("check", "gate", {"ask": "Good?", "entry": {"approve": "shipped"}})
    library(runner, wf_reply=[WORK, wrong])
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [fan()])
    wait_for(lambda: [c.status for c in kids(runner, parent.id)] == ["waiting_gate"])
    (child,) = kids(runner, parent.id)
    with pytest.raises(ValueError, match="'shipped' is not a status of tickets"):
        runner.decide(child.id, person=True)
    assert fetch(runner, child.id).status == "waiting_gate"


def test_publishing_moves_the_held_entry_to_the_published_status_with_its_links(
    lists: WorldLists,
) -> None:
    lists.declare(WORLD, "tickets", STATUSES, published="closed")
    lists.claim(WORLD, "tickets", "run_1", order="rank")  # t-b
    lists.published(WORLD, "run_1", {"forum": "https://f.example/1"})
    (moved,) = lists.published(WORLD, "run_1", {"mail": "https://m.example/1"})
    assert (moved.key, moved.status) == ("t-b", "closed")
    assert moved.fields["links"] == {"forum": "https://f.example/1", "mail": "https://m.example/1"}
    with pytest.raises(ValueError, match="published must be declared"):
        lists.declare(WORLD, "tickets", STATUSES, published="sent")


def test_an_item_that_cannot_start_leaves_its_entry_with_the_parent_until_it_ends(
    runner: Runner, lists: WorldLists, tmp_path: Path
) -> None:
    """t-c's file goes after the claim; t-a is handed to another run meanwhile. Neither Item
    starts, neither Entry is left with a run id that never ends."""
    library(runner, wf_reply=[WORK, CHECK])
    stage = fan(take=3)
    stage = StageDef(stage.name, stage.type, {**stage.config, "max_parallel": 1})
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [stage])
    wait_for(lambda: [c.status for c in kids(runner, parent.id)] == ["waiting_gate"])
    (tmp_path / "worlds" / WORLD / "tickets" / "t-c.md").unlink()
    lists.hand(WORLD, "tickets", "t-a", parent.id, "run_other")

    runner.decide(kids(runner, parent.id)[0].id, person=True)
    waiting = at(runner, parent.id, "waiting_gate")
    items = {i.key: (i.status, i.error or "") for i in waiting.stages[0].items}
    assert items["t-c"][0] == "failed" and "cannot give 'Ticket t-c'" in items["t-c"][1]
    assert items["t-a"] == ("failed", "Entry 't-a' of tickets is held by run run_other")
    assert sorted(c.status for c in kids(runner, parent.id)) == ["aborted", "done"]
    assert lists.get(WORLD, "tickets", "t-c").claimed_by == parent.id
    runner.abort(parent.id)
    at(runner, parent.id, "aborted")
    assert lists.get(WORLD, "tickets", "t-c").claimed_by is None
    assert lists.get(WORLD, "tickets", "t-a").claimed_by == "run_other"


def test_a_send_back_to_the_fan_out_works_the_same_entry_again(
    runner: Runner, lists: WorldLists
) -> None:
    library(runner, wf_reply=[WORK])
    again = StageDef("again", "gate", {"ask": "Again?"})
    parent = runner.start(WORLD, "wf_p", 1, "Inbox", [fan(on_finish="closed"), again])
    at(runner, parent.id, "waiting_gate")
    assert lists.get(WORLD, "tickets", "t-b").status == "closed"
    runner.decide(parent.id, send_back_to="each ticket", note="Shorter")
    wait_for(lambda: len(kids(runner, parent.id)) == 2)
    at(runner, parent.id, "waiting_gate")
    first, second = sorted(kids(runner, parent.id), key=lambda c: c.created_at)
    assert first.name == second.name == "reply: Ticket t-b"
    assert [lists.get(WORLD, "tickets", k).runs for k in ("t-a", "t-c")] == [[], []]
