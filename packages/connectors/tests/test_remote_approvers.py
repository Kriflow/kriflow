"""Remote Approvals on the connectors side: a notify package offering the Capability is used
only where a world enables it with its Setup done, stays off by default when its Manifest says
so, and its long-lived process is wanted once per distinct set of Setup values."""

from pathlib import Path

import pytest

from kriflow_connectors import Connectors, FakeTransport
from kriflow_connectors.fake import default_list

FIXTURES = Path(__file__).parent / "fixtures"


def test_a_remote_approver_is_used_only_where_its_setup_is_done(
    connectors: Connectors,
    transport: FakeTransport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed = default_list(tmp_path / "packages", {"pager": FIXTURES / "packages/pager"})
    monkeypatch.setenv("SWARMIFY_DEFAULT_PACKAGES", str(listed))
    assert connectors.install_defaults() == {}  # installed, but no world is given it
    connectors.offer_defaults("w1")
    assert connectors.enabled("w1") == {}

    for world in ("w1", "w2", "w3"):
        connectors.enable(world, "pager", "1.0.0")
    assert connectors.remote_approvers("w1") == [] and connectors.listeners() == []
    connectors.save_setup("w1", "pager", {"token": "t"}, {"room": "ops"})
    connectors.save_setup("w2", "pager", {}, {"room": "ops"})  # same values as w1
    connectors.save_setup("w3", "pager", {}, {"room": "news"})

    (pager,) = connectors.remote_approvers("w1")
    assert pager.kind == "notify" and pager.stage_types == []
    assert connectors.ask(pager, "post_gate", {"gate": {"run_id": "r"}}) == {"ok": True}
    assert transport.calls[-1] == (
        "pager",
        "post_gate",
        {"gate": {"run_id": "r"}},
        {"PAGER_TOKEN": "t", "PAGER_ROOM": "ops"},
    )
    wanted = sorted(env["PAGER_ROOM"] for _, env in connectors.listeners())
    assert wanted == ["news", "ops"]  # w1 and w2 share one process
    connectors.disable("w3", "pager")
    assert [env["PAGER_ROOM"] for _, env in connectors.listeners()] == ["ops"]
