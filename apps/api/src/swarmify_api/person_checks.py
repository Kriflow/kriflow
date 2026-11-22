"""HTTP face of Person Checks (person_check.py): the open ones, for Needs you and the run page,
their screenshots, and a person's answer. Answered here only, never through Slack or another
Remote Approval, so a code never leaves the box (spec open question 8)."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from swarmify_connectors.person_check import PersonCheck, PersonChecks

RunLabel = Callable[[str, str], tuple[str, str] | None]


class PersonCheckView(BaseModel):
    id: str
    world_id: str
    run_id: str
    run_name: str | None
    stage_id: str
    stage_name: str | None
    package: str
    kind: Literal["code", "look"]
    """`code`: type the code the site sent; `look`: Try again or Stop."""
    ask: str
    """The site's ask, as the process read it."""
    site: str | None
    """What it signs in to: the stage's login's first domain."""
    has_screenshot: bool
    opened_at: str
    closes_at: str
    """When it fails the stage unanswered."""


class PersonAnswer(BaseModel):
    code: str | None = None
    action: Literal["try_again", "stop"] | None = None


def view(check: PersonCheck, label: RunLabel, timeout: float) -> PersonCheckView:
    names = label(check.run_id, check.stage_id)

    def iso(t: float) -> str:
        return datetime.fromtimestamp(t, UTC).isoformat()

    return PersonCheckView(
        id=check.id,
        world_id=check.world_id,
        run_id=check.run_id,
        run_name=names[0] if names else None,
        stage_id=check.stage_id,
        stage_name=names[1] if names else None,
        package=check.package,
        kind="code" if check.kind == "code" else "look",
        ask=check.ask,
        site=check.site,
        has_screenshot=check.screenshot is not None,
        opened_at=iso(check.opened_at),
        closes_at=iso(check.opened_at + timeout),
    )


def router(checks: PersonChecks, label: RunLabel) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["person checks"])

    def found(check_id: str) -> PersonCheck:
        check = checks.get(check_id)
        if check is None:
            raise HTTPException(404, "This check is no longer open.")
        return check

    @api.get("/person-checks")
    def open_checks(world_id: str | None = None) -> list[PersonCheckView]:
        """Every open Person Check, oldest first; one world's with `world_id`."""
        return [view(c, label, checks.timeout) for c in checks.open_checks(world_id)]

    @api.get("/runs/{run_id}/person-checks")
    def run_checks(run_id: str) -> list[PersonCheckView]:
        return [view(c, label, checks.timeout) for c in checks.open_checks(run_id=run_id)]

    @api.get("/person-checks/{check_id}/screenshot", response_class=FileResponse)
    def screenshot(check_id: str) -> FileResponse:
        shot = found(check_id).screenshot
        if shot is None or not shot.is_file():
            raise HTTPException(404, "This check has no screenshot.")
        return FileResponse(shot, headers={"Cache-Control": "no-store"})

    @api.post("/person-checks/{check_id}", status_code=204)
    def answer(check_id: str, body: PersonAnswer) -> None:
        """A code (kind `code`), or `try_again` or `stop`. 404 once it is closed, 422 for an
        answer that does not fit it. The code is never returned or put in an event."""
        found(check_id)
        try:
            checks.answer(check_id, body.code, body.action)
        except KeyError:
            raise HTTPException(404, "This check is no longer open.") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    return api
