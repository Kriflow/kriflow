"""The fake_publish fixture's answers, called in-process by the core's tests: `preview_of` as its
`preview` tool would answer and `claimed`, the posts its `outcomes` counts. It runs no server."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

EDITABLE = ("title", "description")
VISIBILITIES = ("public", "unlisted", "private")
TITLE_MAX = 100
NOTICE = "_notice"
"""The outcomes answer's key for a word to the operator, as a real package says why it could
not read something."""
SCOPE_NOTICE = "Retention is not read. Sign in to Fake Publish again to get retention."
TOKEN = {"FAKE_PUBLISH_TOKEN": "at"}
"""What its process gets once its account is signed in."""


def _field(key: str, label: str, value: Any, hint: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "editable": key in EDITABLE, "hint": hint}


def preview_of(args: Mapping[str, Any]) -> dict[str, Any]:
    """What the stage will publish, the metadata's frontmatter and the gate's edits applied,
    plus every problem that would stop it."""
    metadata = args.get("metadata") or {}
    params = {
        **(args.get("params") or {}),
        **{k: metadata[k] for k in EDITABLE if metadata.get(k)},
        **{k: v for k, v in (args.get("edits") or {}).items() if k in EDITABLE},
    }
    title = str(params.get("title") or "").strip()
    visibility = str(params.get("visibility") or "private")
    problems, schedule = [], None
    if not title and not params.get("metadata"):
        problems.append("a publish stage needs params.title")
    elif len(title) > TITLE_MAX:
        problems.append(f"the title is {len(title)} characters, the limit is {TITLE_MAX}")
    if visibility not in VISIBILITIES:
        problems.append(f"visibility is one of {', '.join(VISIBILITIES)}, got {visibility!r}")
    if params.get("schedule"):
        when = datetime.fromisoformat(str(params["schedule"]))
        schedule = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if visibility != "public":
            problems.append("a scheduled post goes public at its time; set visibility: public")
    inputs = args.get("inputs") or []
    if not inputs:
        problems.append("a publish stage needs an input: the video to post")
    fields = [
        _field("title", "Title", title),
        _field("description", "Description", str(params.get("description") or "").strip()),
        _field("visibility", "Visibility", visibility),
        _field("schedule", "When", schedule, "" if schedule else "Right after confirming."),
        _field("video", "Video", inputs[0] if inputs else None),
    ]
    return {"fields": fields, "problems": problems}


def claimed(posts: list[dict[str, Any]]) -> list[str]:
    """The ids of the posts it made, which its `outcomes` answers for."""
    return [str(p["id"]) for p in posts if p.get("connector") == "fake_publish" and p.get("id")]
