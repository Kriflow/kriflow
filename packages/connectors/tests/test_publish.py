"""Publish stages at the Connectors seam: the preview a gate shows and the check of a saved stage,
the call that sends exactly what was confirmed, the normalized result, the refusals and the
outcomes Capability, for a
package's publish connector answering over its own tools. Real SQLite, the recording fake
transport. YouTube's and Upload-Post's own previews are tested in their packages."""

import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from swarmify_connectors import (
    Call,
    ConnectorError,
    Connectors,
    FakeTransport,
    Packages,
    Unsure,
    publish,
)

FIXTURES = Path(__file__).parent / "fixtures"

W = "world_1"
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
SENT = {"channel": "#news", "text": "Hi"}


def values(shown: dict[str, Any]) -> dict[str, Any]:
    return {f["key"]: f["value"] for f in shown["fields"]}


def poster(connectors: Connectors) -> None:
    """A world's own text-publish connector: a webhook that posts a message."""
    connectors.add(
        connectors.draft(W, "webhook", "publish", "webhook-mcp", ["HOOK_URL"], ["connector"]),
        {"HOOK_URL": "https://hooks.example/1"},
    )


def stage() -> dict[str, Any]:
    """A publish stage's config as it reaches `run_stage`: with the values its gate confirmed."""
    params = {"channel": "#news", "input_path": "/runs/r/post.md"}
    return {"calls": "webhook", "input": "post.md", "params": params, "confirmed": SENT}


def test_a_publish_stage_no_gate_confirmed_never_calls(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    unconfirmed = {k: v for k, v in stage().items() if k != "confirmed"}
    with pytest.raises(ConnectorError, match="publishes only the values a gate confirmed"):
        connectors.run_stage(W, "run_1", "stage_1", unconfirmed)
    assert transport.calls == []


def interrupted(connectors: Connectors, name: str = "webhook", **kw: Any) -> None:
    """A publish call of stage_1 the api was cut off in: left running in the call ledger."""
    now = NOW.isoformat()
    request = {"idempotency_key": "stage_1"}
    connectors.storage.save_call(
        Call("stage_1", W, "run_1", name, "publish", request, "running", now, now, attempts=1, **kw)
    )


def resumer(connectors: Connectors, tmp_path: Path) -> None:
    """A publish package that declares its publish resumes safely (`safe_resume`)."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "packages/clicks", repo / "connectors/clicks")
    manifest = repo / "connectors/clicks/swarmify-connector.toml"
    manifest.write_text(
        manifest.read_text().replace(
            '"publish", "outcomes"', '"publish", "outcomes", "safe_resume"'
        )
    )
    connectors.packages = Packages(connectors.packages.root, repo)
    connectors.install_defaults()
    connectors.enable(W, "clicks", "1.0.0")
    connectors.save_setup(W, "clicks", {"token": "t"}, {})


def clicks_stage() -> dict[str, Any]:
    return {**stage(), "calls": "clicks", "confirmed": {"platforms": ["short", "long"]}}


def test_an_interrupted_publish_of_a_package_that_cannot_resume_asks_instead(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    for status in ("running", "failed"):  # cut off mid-call, or its error reached the ledger
        interrupted(connectors)
        connectors.storage.save_call(replace(connectors.call_for("stage_1"), status=status))  # type: ignore[arg-type]
        with pytest.raises(Unsure) as asked:
            connectors.run_stage(W, "run_1", "stage_1", stage())
        assert str(asked.value) == (
            "webhook was cut off while posting and could not check whether it posted: it does"
            " not say whether it can resume safely. Look for it on webhook, then answer."
        )
    assert transport.calls == []
    connectors.run_stage(W, "run_1", "stage_1", stage(), visit=2)  # a send-back reruns it
    assert transport.calls[0][2]["idempotency_key"] == "stage_1.2"


def test_a_package_that_resumes_safely_is_called_again_with_the_same_key(
    connectors: Connectors, transport: FakeTransport, tmp_path: Path
) -> None:
    resumer(connectors, tmp_path)
    interrupted(connectors, "clicks")
    transport.outputs["publish"] = [{"url": "https://sho.rt/p1", "already_posted": True}]
    call = connectors.run_stage(W, "run_1", "stage_1", clicks_stage())
    ((_, tool, args, _),) = transport.calls
    assert (tool, args["idempotency_key"], call.attempts) == ("publish", "stage_1", 2)
    out = connectors.publish_result(call)
    assert (out["links"], out["already_posted"]) == ({"clicks": "https://sho.rt/p1"}, True)


def test_an_unsure_answer_asks_and_is_never_retried_by_itself(
    connectors: Connectors, transport: FakeTransport, tmp_path: Path
) -> None:
    resumer(connectors, tmp_path)
    transport.outputs["publish"] = [ConnectorError("unsure: the upload session did not answer")]
    ask = (
        "Clicks was cut off while posting and could not check whether it posted: the upload"
        " session did not answer. Look for it on Short and Long, then answer."
    )
    for _ in range(2):  # the second, a replay of the stage, asks without calling
        with pytest.raises(Unsure) as asked:
            connectors.run_stage(W, "run_1", "stage_1", clicks_stage())
        assert str(asked.value) == ask
    assert len(transport.calls) == 1


def test_the_answers_at_the_gate_record_the_links_or_post_under_a_new_key(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    interrupted(connectors)
    connectors.answer_unsure("stage_1", None)  # Post it now
    transport.outputs["publish"] = [ConnectorError("unsure: no answer")]
    with pytest.raises(Unsure):
        connectors.run_stage(W, "run_1", "stage_1", stage())
    connectors.answer_unsure("stage_1", None)  # each time under a key of its own
    transport.outputs["publish"] = [{"url": "https://hooks.example/m1"}]
    connectors.run_stage(W, "run_1", "stage_1", stage())
    keys = [c[2]["idempotency_key"] for c in transport.calls]
    assert len(set(keys)) == 2 and "stage_1" not in keys

    interrupted(connectors)
    connectors.answer_unsure("stage_1", {"hooks": "https://hooks.example/m1"})  # It posted
    call = connectors.run_stage(W, "run_1", "stage_1", stage())
    assert len(transport.calls) == 2  # recorded, never posted
    out = connectors.publish_result(call)
    assert (out["links"], out["recorded_by"]) == ({"hooks": "https://hooks.example/m1"}, "person")
    with pytest.raises(KeyError):
        connectors.answer_unsure("stage_9", None)


def test_publish_previews_only_for_publish_connectors(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    transport.outputs["preview"] = [{"fields": [{"key": "text", "value": "Hi"}]}]
    shown = connectors.publish_preview(W, stage())
    assert shown is not None and shown["connector"] == "webhook"
    assert values(shown) == {"text": "Hi"}
    assert connectors.publish_preview(W, {"calls": "remotion_render"}) is None
    assert connectors.publish_preview(W, {"calls": "nope"}) is None


def test_outcomes_come_from_each_publish_connector_offering_them_with_its_setup(
    connectors: Connectors, transport: FakeTransport, tmp_path: Path
) -> None:
    poster(connectors)  # a publish connector without the Capability is not asked
    assert connectors.outcome_reporters(W) == []
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "packages/clicks", repo / "connectors/clicks")
    connectors.packages = Packages(connectors.packages.root, repo)
    connectors.install_defaults()
    connectors.enable(W, "clicks", "1.0.0")
    (clicks,) = connectors.outcome_reporters(W)
    assert clicks.manifest["primary_metric"] == "clicks"

    posts = [{"connector": "clicks", "id": "p1", "links": {"clicks": "https://sho.rt/p1"}}]
    with pytest.raises(ConnectorError, match="clicks needs its Setup"):
        connectors.outcomes(clicks, posts)
    connectors.save_setup(W, "clicks", {"token": "t"}, {})
    transport.outputs["outcomes"] = [
        {"p1": {"clicks": 12, "rate": 0.5}},
        {"p1": {"clicks": 1, "visible": False, "visibility_label": "draft"}, "_notice": "again"},
        {"p1": 3},
        {"p1": {"x": True}},
        {"p1": {"visible": "no"}},
        {"p1": {"visibility_label": 2}},
        {"p1": {"privacy": "private"}},  # a platform's own word is no metric
        {"p1": {"clicks": 1}, "_notice": 5},
        {"p1": {"clicks": 2, "curves": {"kept": [[0, 1], [0.5, 0.25]]}}},
        {"p1": {"curves": [[0, 1]]}},  # a curve needs its name
        {"p1": {"curves": {"kept": [[0, 1, 2]]}}},  # a point is [x, y]
        {"p1": {"curves": {"kept": [["a", 1]]}}},
    ]
    assert connectors.outcomes(clicks, posts) == ({"p1": {"clicks": 12, "rate": 0.5}}, None)
    assert transport.calls[-1] == ("clicks", "outcomes", {"posts": posts}, {"CLICKS_TOKEN": "t"})
    # whether everyone can see the post is a boolean, with the platform's word for it as a
    # label; `_notice` says what the connector could not read
    found = ({"p1": {"clicks": 1, "visible": False, "visibility_label": "draft"}}, "again")
    assert connectors.outcomes(clicks, posts) == found
    for _ in range(6):  # anything but numbers by metric by post
        with pytest.raises(ConnectorError, match="clicks outcomes answered"):
            connectors.outcomes(clicks, posts)
    # beside its numbers a post may carry curves: named lists of [x, y] points
    curve = {"kept": [[0, 1], [0.5, 0.25]]}
    assert connectors.outcomes(clicks, posts) == ({"p1": {"clicks": 2, "curves": curve}}, None)
    for _ in range(3):
        with pytest.raises(ConnectorError, match="clicks outcomes answered"):
            connectors.outcomes(clicks, posts)


def test_a_saved_publish_stage_is_checked_by_its_own_preview(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    config = {"calls": "webhook", "input": "post.md", "params": {}}
    transport.outputs["preview"] = [{"fields": [], "problems": ["webhook needs params.channel"]}]
    assert connectors.stage_problems(W, config) == ["webhook needs params.channel"]
    # one it cannot answer is the gate's to show; its Manifest is checked meanwhile
    for answer in (ConnectorError("webhook: could not start"), {"nothing": True}):
        transport.outputs["preview"] = [answer]
        assert connectors.stage_problems(W, config) == []
        assert connectors.stage_problems(W, {**config, "input": None}) == []
    # a preview needs no account: it is asked with what Setup there is
    connectors.setups.put_value("webhook", None, "hook_url", None)
    transport.outputs["preview"] = [{"fields": [], "problems": []}]
    assert connectors.stage_problems(W, config) == []
    assert transport.calls[-1][1] == "preview" and transport.calls[-1][3] == {}
    assert connectors.stage_problems(W, {"calls": "web_search"}) == []
    assert connectors.stage_problems(W, {"calls": "nope"}) == []


def test_a_worlds_publish_connector_previews_and_publishes_through_its_own_tools(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    fields = [
        {"key": "channel", "label": "Channel", "value": "#news"},
        {"key": "text", "label": "Message", "value": "Hello", "editable": True},
    ]
    transport.outputs["preview"] = [{"fields": fields, "problems": []}]
    config = {"calls": "webhook", "input": "post.md", "params": {"channel": "#news"}}
    shown = connectors.publish_preview(W, config, {"text": "Hello"}, {"text": "Hi"})
    assert shown == {
        "connector": "webhook",
        "fields": [
            {"key": "channel", "label": "Channel", "value": "#news", "editable": False, "hint": ""},
            {"key": "text", "label": "Message", "value": "Hi", "editable": True, "hint": ""},
        ],
        "problems": [],
    }  # the edit stands though this fake connector ignored it
    ((name, tool, args, env),) = transport.calls
    assert (name, tool, env) == ("webhook", "preview", {"HOOK_URL": "https://hooks.example/1"})
    assert args == {
        "inputs": ["post.md"],
        "params": {"channel": "#news"},
        "metadata": {"text": "Hello"},
        "edits": {"text": "Hi"},
    }
    assert connectors.stage_problems(W, config) == []  # its preview found nothing wrong

    transport.outputs["publish"] = [
        {
            "url": "https://hooks.example/m/1",
            "id": "m1",
            "status": "scheduled",
            "public": True,
            "scheduled_for": "2099-01-01T09:00:00Z",
        }
    ]
    sent = {"channel": "#news", "text": "Hi"}
    params = {**config["params"], "input_path": "/runs/r/post.md"}
    call = connectors.run_stage(
        W, "run_1", "stage_1", {**config, "params": params, "confirmed": sent}
    )
    assert transport.calls[-1][1:3] == (
        "publish",
        {"fields": sent, "idempotency_key": "stage_1", "input_path": "/runs/r/post.md"},
    )
    # who sees it and when are the connector's to say, in words core need not know
    assert connectors.publish_result(call) == {
        "status": "scheduled",
        "links": {"webhook": "https://hooks.example/m/1"},
        "id": "m1",
        "failed": {},
        "public": True,
        "scheduled_for": "2099-01-01T09:00:00Z",
        "already_posted": False,
        "recorded_by": None,
    }
    unsaid = publish.result("webhook", {"url": "u", "public": "yes", "scheduled_for": 5})
    assert (unsaid["public"], unsaid["scheduled_for"]) == (None, None)


def test_a_publish_long_job_is_polled_like_any_and_its_last_answer_is_what_was_posted(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    transport.outputs["publish"] = [{"job_id": "req_9", "poll": "post_status"}] * 2
    links = {"youtube": "https://youtu.be/abc123"}
    transport.outputs["post_status"] = [
        {"status": "running"},
        {"status": "done", "links": links, "failed": {"tiktok": "Expired access token"}},
        {"status": "done", "links": {}, "failed": {"youtube": "quota"}},
    ]
    config = {"calls": "webhook", "input": "post.md", "params": {}, "confirmed": {"text": "Hi"}}
    call = connectors.run_stage(W, "run_1", "stage_1", config)
    assert call.status == "done" and call.polls == 2
    assert [c[1:3] for c in transport.calls[1:]] == [("post_status", {"job_id": "req_9"})] * 2
    out = connectors.publish_result(call)
    assert (out["links"], out["failed"]) == (links, {"tiktok": "Expired access token"})
    with pytest.raises(ConnectorError, match="nothing was posted. youtube: quota"):
        connectors.run_stage(W, "run_1", "stage_2", config)
    assert connectors.call_for("stage_2").status == "failed"  # type: ignore[union-attr]


def test_a_post_that_reached_no_platform_fails_the_stage(
    connectors: Connectors, transport: FakeTransport
) -> None:
    poster(connectors)
    transport.outputs["publish"] = [{"links": {}, "failed": {"webhook": "410 Gone"}}]
    config = {"calls": "webhook", "input": "post.md", "params": {}, "confirmed": {"text": "Hi"}}
    with pytest.raises(ConnectorError, match="nothing was posted. webhook: 410 Gone"):
        connectors.run_stage(W, "run_1", "stage_1", config)


@pytest.mark.parametrize(
    ("answer", "problem"),
    [
        (ConnectorError("webhook: could not start"), "webhook preview failed: webhook: could"),
        ({"nothing": True}, "preview answered without fields"),
        ({"fields": [{"label": "No key"}]}, "preview answered a field without a key"),
        ({"fields": [], "problems": ["no channel"]}, "no channel"),
    ],
)
def test_a_preview_that_cannot_be_had_is_a_problem(
    connectors: Connectors, transport: FakeTransport, answer: Any, problem: str
) -> None:
    poster(connectors)
    transport.outputs["preview"] = [answer]
    shown = connectors.publish_preview(W, {"calls": "webhook", "params": {}})
    assert shown is not None and any(problem in p for p in shown["problems"])
