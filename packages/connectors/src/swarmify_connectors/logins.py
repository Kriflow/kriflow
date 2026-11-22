"""World Logins and the Secrets Store (spec: World Logins And Browser Connector).

A World Login is named sign-in details a world keeps for a website. Its name, domains and
username are state in SQLite; its password, authenticator (TOTP) secret and Saved Session are
kept behind the Secrets Store, under `logins/<world>/<login>/`, and never returned by any API.
Only the core reads the store, to hand a stage the login it names.

The Secrets Store is one port (ADR 0012: 1Password or Infisical are the realistic second
adapters); its default backend is `FileSecrets`, a mode-600 JSON file in the data directory built
like the Setup file, which every sandboxed process is denied.
"""

import base64
import binascii
import hashlib
import hmac
import re
import struct
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from swarmify_connectors.setup import PrivateJson
from swarmify_kernel import Event

NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
"""A login's name, short enough to write unquoted in a stage: `login: shop_admin`."""
_HOST = re.compile(r"^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*(:\d{1,5})?$")
RESULTS = ("signed_in", "reused_session", "person_check", "refused_origin", "failed", "received")
"""What a stage's use of a login came to, as its `login.used` event says."""


class SecretStore(Protocol):
    """Secrets by ref, a path-like string (`logins/<world>/<login>/password`). A read-only
    backend may refuse `put`; Saved Sessions always use the file backend, as the box writes
    them."""

    def get(self, ref: str) -> str | None: ...

    def put(self, ref: str, value: str) -> None: ...

    def delete(self, ref: str) -> None: ...

    def delete_prefix(self, prefix: str) -> None: ...


class FileSecrets(PrivateJson):
    """The default Secrets Store: `{ref: value}` in one mode-600 file, refused when group or
    others can read it."""

    def get(self, ref: str) -> str | None:
        value = self.read().get(ref)
        return value if isinstance(value, str) else None

    def put(self, ref: str, value: str) -> None:
        # ponytail: read-modify-write without a lock, as the Setup file; two writes at once can
        # lose one. A lock file when parallel stages save sessions often.
        data = self.read()
        data[ref] = value
        self._write(data)

    def delete(self, ref: str) -> None:
        data = self.read()
        if data.pop(ref, None) is not None:
            self._write(data)

    def delete_prefix(self, prefix: str) -> None:
        data = self.read()
        kept = {k: v for k, v in data.items() if not k.startswith(prefix)}
        if len(kept) != len(data):
            self._write(kept)


def domain_problem(domain: str) -> str | None:
    """Why a login's domain is refused, as the sandbox's rules would refuse it: an exact host
    with an optional port, or `*.example.com` for its strict subdomains; never `*` or `*.com`."""
    if not _HOST.fullmatch(domain):
        return f"{domain or 'an empty line'} is not a host like admin.myshop.com"
    if domain.startswith("*.") and "." not in domain[2:].split(":")[0]:
        return f"{domain} is too broad"
    return None


def clean_domains(raw: Iterable[str]) -> list[str]:
    """The domains lower-cased, trimmed and each once. Raises ValueError naming the first bad
    one, or when there are none."""
    out = list(dict.fromkeys(d.strip().lower() for d in raw if d.strip()))
    if not out:
        raise ValueError("Domains: give at least one")
    for d in out:
        if problem := domain_problem(d):
            raise ValueError(f"Domains: {problem}")
    return out


def one_line(label: str, value: str) -> str:
    """A non-empty single line. Raises ValueError naming the field, never the value."""
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{label}: give one line")
    return value


def totp_secret(raw: str) -> str:
    """An authenticator secret as base32, spaces taken out and upper-cased, once it makes a
    code. Raises ValueError, never naming the value."""
    secret = "".join(raw.split()).upper()
    try:
        totp(secret)
    except (binascii.Error, ValueError):
        raise ValueError("Authenticator secret: the text under the QR code, letters A to Z and"
                         " digits 2 to 7") from None  # fmt: skip
    return secret


def totp(secret: str, at: float | None = None) -> str:
    """The six-digit code for `secret` at `at` (now by default): RFC 6238, HMAC-SHA1, 30 s."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    if not key:
        raise ValueError("an empty secret")
    counter = struct.pack(">Q", int((time.time() if at is None else at) // 30))
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


@dataclass(frozen=True)
class Login:
    """A World Login as anyone may see it: never a password, TOTP secret or session."""

    world_id: str
    name: str
    domains: list[str]
    username: str
    created_at: str
    updated_at: str
    rotated_at: str
    has_totp: bool = False
    has_session: bool = False
    last_used_at: str | None = None


@dataclass(frozen=True)
class Use:
    """One stage's use of a login, from its `login.used` event."""

    run_id: str
    stage_id: str
    package: str
    result: str
    at: str


@dataclass(frozen=True)
class Handed:
    """What a stage that names a login is handed: only its process ever sees these."""

    login: Login
    password: str
    totp: str | None
    session: str | None


class Taken(ValueError):
    """A login of that name exists in the world already."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def ref(world_id: str, name: str, secret: str = "") -> str:
    return f"logins/{world_id}/{name}/{secret}"


class LoginRows(Protocol):
    """The logins' state, and the events about them, in the Connectors storage."""

    def logins(self, world_id: str) -> list[dict[str, Any]]: ...

    def add_login(self, row: dict[str, Any], event: Event) -> bool: ...

    def update_login(
        self, world_id: str, name: str, fields: dict[str, Any], event: Event
    ) -> bool: ...

    def delete_login(self, world_id: str, name: str, event: Event) -> bool: ...

    def append_event(self, event: Event) -> None: ...

    def login_uses(self, world_id: str, name: str | None, limit: int) -> list[Event]: ...


class Logins:
    def __init__(self, rows: LoginRows, secrets: SecretStore) -> None:
        self.rows = rows
        self.secrets = secrets

    def list_all(self, world_id: str) -> list[Login]:
        last: dict[str, str] = {}
        for e in self.rows.login_uses(world_id, None, 1000):
            last.setdefault(str(e.payload.get("login")), e.ts)

        def has(name: str, secret: str) -> bool:
            return self.secrets.get(ref(world_id, name, secret)) is not None

        return [
            Login(
                world_id,
                r["name"],
                list(r["domains"]),
                r["username"],
                r["created_at"],
                r["updated_at"],
                r["rotated_at"],
                has_totp=has(r["name"], "totp"),
                has_session=has(r["name"], "session"),
                last_used_at=last.get(r["name"]),
            )
            for r in self.rows.logins(world_id)
        ]

    def get(self, world_id: str, name: str) -> Login | None:
        return next((x for x in self.list_all(world_id) if x.name == name), None)

    def _get(self, world_id: str, name: str) -> Login:
        found = self.get(world_id, name)
        if found is None:
            raise KeyError(name)
        return found

    def add(
        self,
        world_id: str,
        name: str,
        domains: Iterable[str],
        username: str,
        password: str,
        totp_secret_: str | None = None,
    ) -> Login:
        """Raises ValueError naming the bad field, Taken for a name in use."""
        if not NAME.fullmatch(name):
            raise ValueError("Name: lower-case letters, digits and _, starting with a letter")
        row = {
            "world_id": world_id,
            "name": name,
            "domains": clean_domains(domains),
            "username": one_line("Username", username.strip()),
        }
        one_line("Password", password)
        secret = totp_secret(totp_secret_) if totp_secret_ and totp_secret_.strip() else None
        now = _now()
        row |= {"created_at": now, "updated_at": now, "rotated_at": now}
        said = {"login": name, "domains": row["domains"], "username": row["username"]}
        if not self.rows.add_login(row, self._event("login.added", world_id, said)):
            raise Taken(f"Name: {name} is taken in this world")
        self.secrets.put(ref(world_id, name, "password"), password)
        if secret:
            self.secrets.put(ref(world_id, name, "totp"), secret)
        return self._get(world_id, name)

    def change(
        self,
        world_id: str,
        name: str,
        domains: Iterable[str] | None = None,
        username: str | None = None,
    ) -> Login:
        """Its domains or username, never a secret. Raises KeyError, ValueError."""
        before = self._get(world_id, name)
        fields: dict[str, Any] = {}
        if domains is not None and (cleaned := clean_domains(domains)) != before.domains:
            fields["domains"] = cleaned
        if username is not None and (user := one_line("Username", username.strip())) != (
            before.username
        ):
            fields["username"] = user
        if fields:
            said = {"login": name, **fields}
            self.rows.update_login(
                world_id, name, {**fields, "updated_at": _now()},
                self._event("login.changed", world_id, said),
            )  # fmt: skip
        return self._get(world_id, name)

    def rotate(
        self,
        world_id: str,
        name: str,
        password: str,
        totp_secret_: str | None = None,
        clear_totp: bool = False,
    ) -> Login:
        """A new password (and authenticator secret, or none with `clear_totp`); its Saved Session
        is forgotten, so an old one never stays signed in. Raises KeyError, ValueError."""
        self._get(world_id, name)
        one_line("Password", password)
        secret = totp_secret(totp_secret_) if totp_secret_ and totp_secret_.strip() else None
        self.secrets.put(ref(world_id, name, "password"), password)
        if secret:
            self.secrets.put(ref(world_id, name, "totp"), secret)
        elif clear_totp:
            self.secrets.delete(ref(world_id, name, "totp"))
        self.secrets.delete(ref(world_id, name, "session"))
        now = _now()
        totp_said = "replaced" if secret else ("cleared" if clear_totp else "kept")
        said = {"login": name, "authenticator": totp_said}
        self.rows.update_login(
            world_id, name, {"updated_at": now, "rotated_at": now},
            self._event("login.rotated", world_id, said),
        )  # fmt: skip
        return self._get(world_id, name)

    def forget_session(self, world_id: str, name: str) -> Login:
        self._get(world_id, name)
        self.secrets.delete(ref(world_id, name, "session"))
        self.rows.append_event(self._event("login.session_forgotten", world_id, {"login": name}))
        return self._get(world_id, name)

    def delete(self, world_id: str, name: str) -> bool:
        """The row and every secret under it. The caller refuses one a workflow names."""
        event = self._event("login.deleted", world_id, {"login": name})
        if not self.rows.delete_login(world_id, name, event):
            return False
        self.secrets.delete_prefix(ref(world_id, name))
        return True

    def delete_world(self, world_id: str) -> None:
        """Every secret of the world's logins; their rows go with the Connectors storage's."""
        self.secrets.delete_prefix(f"logins/{world_id}/")

    def uses(self, world_id: str, name: str, limit: int = 20) -> list[Use]:
        return [
            Use(
                str(p.get("run_id")),
                str(p.get("stage_id")),
                str(p.get("package")),
                str(p.get("result")),
                e.ts,
            )
            for e in self.rows.login_uses(world_id, name, limit)
            if (p := e.payload)
        ]

    # A stage that names a login (the core only)

    def hand(self, world_id: str, name: str) -> Handed:
        """The login's values for the one stage process that names it. Raises KeyError when the
        world has no such login, or it has lost its password."""
        login = self._get(world_id, name)
        password = self.secrets.get(ref(world_id, name, "password"))
        if password is None:
            raise KeyError(name)
        totp_ = self.secrets.get(ref(world_id, name, "totp"))
        return Handed(login, password, totp_, self.secrets.get(ref(world_id, name, "session")))

    def keep_session(self, world_id: str, name: str, value: str) -> None:
        """A session the stage's process saved. Dropped when the login has gone meanwhile."""
        if self.get(world_id, name) is not None:
            self.secrets.put(ref(world_id, name, "session"), value)

    def used(
        self, world_id: str, run_id: str, stage_id: str, package: str, name: str, result: str
    ) -> None:
        """The `login.used` audit event: who used the login and what came of it, no values."""
        payload = {
            "login": name,
            "run_id": run_id,
            "stage_id": stage_id,
            "package": package,
            "result": result if result in RESULTS else "received",
        }
        event = replace(self._event("login.used", world_id, payload), run_id=run_id)
        self.rows.append_event(event)

    @staticmethod
    def _event(kind: str, world_id: str, payload: dict[str, Any]) -> Event:
        by = {} if kind == "login.used" else {"by": "person"}
        return Event.new(kind, world_id=world_id, payload={**payload, **by})
