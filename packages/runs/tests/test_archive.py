"""Archiving runs at the runner's seam: only a finished run archives, its child runs go with
it, archived runs leave the default list, and restore brings the family back."""

import pytest

from runs_fakes import wait_for
from swarmify_runs import Illegal, Pinned, Run, Runner, StageDef

WORLD = "world_a"
REVIEW = StageDef("review", "gate", {"ask": "Approve?", "kind": "approve"})
SUB = StageDef("sub", "workflow", {"runs": "wf_child", "take": []})


def fetch(runner: Runner, run_id: str) -> Run:
    run = runner.get(run_id)
    assert run is not None
    return run


def test_archive_takes_the_family_out_of_the_list_and_restore_brings_it_back(
    runner: Runner,
) -> None:
    runner.pin = lambda _id, _v=None: Pinned(WORLD, "wf_child", 1, "Child", [REVIEW])
    parent = runner.start(WORLD, "wf_p", 1, "Parent", [SUB])
    wait_for(lambda: fetch(runner, parent.id).stages[0].child is not None)
    link = fetch(runner, parent.id).stages[0].child
    assert link is not None
    wait_for(lambda: fetch(runner, link.id).status == "waiting_gate")

    with pytest.raises(Illegal, match="finished"):
        runner.archive(parent.id)
    with pytest.raises(Illegal, match="finished"):
        runner.archive(link.id)

    runner.abort(parent.id)
    wait_for(lambda: fetch(runner, parent.id).status == "aborted")
    wait_for(lambda: fetch(runner, link.id).status == "aborted")
    archived = runner.archive(parent.id)
    assert archived.archived_at is not None
    assert fetch(runner, link.id).archived_at == archived.archived_at
    assert runner.list_all(WORLD, archived=False) == []
    assert {r.id for r in runner.list_all(WORLD, archived=True)} == {parent.id, link.id}
    assert len(runner.list_all(WORLD)) == 2  # no filter: every run, as other readers need

    restored = runner.restore(parent.id)
    assert restored.archived_at is None and fetch(runner, link.id).archived_at is None
    assert {r.id for r in runner.list_all(WORLD, archived=False)} == {parent.id, link.id}
    with pytest.raises(KeyError):
        runner.archive("run_nope")
