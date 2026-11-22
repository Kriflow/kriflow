"""HTTP face of World Logins (spec: World Logins And Browser Connector): list, add, change, rotate,
forget a Saved Session, delete, and recent uses. No response ever carries a password, an
authenticator secret or a session, and a refused body is never echoed back (`no_echo`)."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kriflow_connectors import Logins
from kriflow_connectors.logins import Login, Taken
from kriflow_workflows import Workflows, logins_named
from kriflow_worlds import Worlds

RunLabel = Callable[[str, str], tuple[str, str] | None]
"""A run and stage id to the run's name and the stage's, None for a run that is gone."""


class NewLogin(BaseModel):
    # Every field has a default and is checked by Logins, so a refusal names the field alone and
    # pydantic never echoes the body (which holds the password) in a 422.
    name: str = ""
    domains: list[str] = []
    username: str = ""
    password: str = ""
    totp_secret: str | None = None


class LoginChange(BaseModel):
    domains: list[str] | None = None
    username: str | None = None


class Rotation(BaseModel):
    password: str = ""
    totp_secret: str | None = None
    clear_totp: bool = False


class WorkflowRef(BaseModel):
    id: str
    name: str


class UseView(BaseModel):
    run_id: str
    stage_id: str
    run_name: str | None
    """None for a run deleted since."""
    stage_name: str | None
    package: str
    result: str
    """signed_in, reused_session, person_check, refused_origin, failed or received."""
    at: str


class LoginView(BaseModel):
    name: str
    domains: list[str]
    username: str
    has_totp: bool
    has_session: bool
    created_at: str
    updated_at: str
    rotated_at: str
    """When its password was last set."""
    last_used_at: str | None
    workflows: list[WorkflowRef]
    """The saved workflows whose stages name it."""
    last_use: UseView | None


async def no_echo(request: Request, exc: Exception) -> Response:
    """A 422 for a body the API could not read. On a login's or a Person Check's routes, without
    the `input` pydantic adds: the body holds a password or a code."""
    assert isinstance(exc, RequestValidationError)
    if "/logins" not in request.url.path and "/person-checks" not in request.url.path:
        return await request_validation_exception_handler(request, exc)
    errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


def router(logins: Logins, worlds: Worlds, workflows: Workflows, label: RunLabel) -> APIRouter:
    api = APIRouter(prefix="/api/worlds/{world_id}/logins", tags=["logins"])

    def world_or_404(world_id: str) -> None:
        if worlds.get(world_id) is None:
            raise HTTPException(404, "world not found")

    def naming(world_id: str, name: str) -> list[WorkflowRef]:
        out = []
        for w in workflows.list_all(world_id):
            current = workflows.version(w.id, w.current_version)
            if current is not None and name in logins_named(current.model):
                out.append(WorkflowRef(id=w.id, name=w.name))
        return out

    def use_view(world_id: str, name: str, limit: int) -> list[UseView]:
        out = []
        for u in logins.uses(world_id, name, limit):
            names = label(u.run_id, u.stage_id)
            out.append(
                UseView(
                    **asdict(u),
                    run_name=names[0] if names else None,
                    stage_name=names[1] if names else None,
                )
            )
        return out

    def view(login: Login) -> LoginView:
        last = use_view(login.world_id, login.name, 1)
        fields = {k: v for k, v in asdict(login).items() if k != "world_id"}
        return LoginView(
            **fields,
            workflows=naming(login.world_id, login.name),
            last_use=last[0] if last else None,
        )

    def found(world_id: str, name: str) -> Login:
        world_or_404(world_id)
        login = logins.get(world_id, name)
        if login is None:
            raise HTTPException(404, f"no login named {name} in this world")
        return login

    @api.get("")
    def list_logins(world_id: str) -> list[LoginView]:
        world_or_404(world_id)
        return [view(x) for x in logins.list_all(world_id)]

    @api.post("", status_code=201)
    def add_login(world_id: str, body: NewLogin) -> LoginView:
        """422 naming the bad field (`Domains: *.com is too broad`), 409 for a taken name."""
        world_or_404(world_id)
        try:
            added = logins.add(
                world_id, body.name, body.domains, body.username, body.password, body.totp_secret
            )
        except Taken as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return view(added)

    @api.get("/{name}")
    def get_login(world_id: str, name: str) -> LoginView:
        return view(found(world_id, name))

    @api.patch("/{name}")
    def change_login(world_id: str, name: str, body: LoginChange) -> LoginView:
        """Its domains or username; never a secret."""
        found(world_id, name)
        try:
            return view(logins.change(world_id, name, body.domains, body.username))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.post("/{name}/rotate")
    def rotate_login(world_id: str, name: str, body: Rotation) -> LoginView:
        """A new password (and authenticator secret, or none with `clear_totp`). Its Saved
        Session is forgotten, so it is signed out everywhere on this box."""
        found(world_id, name)
        try:
            rotated = logins.rotate(
                world_id, name, body.password, body.totp_secret, body.clear_totp
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return view(rotated)

    @api.delete("/{name}/session")
    def forget_session(world_id: str, name: str) -> LoginView:
        found(world_id, name)
        return view(logins.forget_session(world_id, name))

    @api.delete("/{name}", status_code=204)
    def delete_login(world_id: str, name: str) -> None:
        """409 while a saved workflow names it, with those workflows (`detail.workflows`)."""
        found(world_id, name)
        if named := naming(world_id, name):
            said = " and ".join(w.name for w in named)
            detail: dict[str, Any] = {
                "message": f"{said} {'name' if len(named) > 1 else 'names'} this login."
                " Remove it from their stages first.",
                "workflows": [w.model_dump() for w in named],
            }
            raise HTTPException(409, detail)
        logins.delete(world_id, name)

    @api.get("/{name}/uses")
    def login_uses(world_id: str, name: str) -> list[UseView]:
        """The latest 20 stages that read it, newest first."""
        found(world_id, name)
        return use_view(world_id, name, 20)

    return api
