"""Operator settings under the product's two names (kriflow#594): `KRIFLOW_<X>` is read first,
then the `SWARMIFY_<X>` an existing `.env` still holds, then the default. A non-empty new value
wins. The old names stop working in a later release (the spec's alias removal), so the api logs
once at startup which of them it found (`old_settings`).
"""

import os
from collections.abc import Mapping
from pathlib import Path

NEW = "KRIFLOW_"
OLD = "SWARMIFY_"
OPERATOR = (
    "API_TOKEN",
    "BASE_URL",
    "CONNECTOR_INDEX",
    "DATA_DIR",
    "DEFAULT_PACKAGES",
    "DRAIN_SECONDS",
    "DRAIN_URL",
    "GRAPH_WRITER",
    "INTERNAL_URL",
    "LOGIN_ALLOW_LOOPBACK",
    "MODEL",
    "NETWORK_WAIT_HOURS",
    "PACKAGE_SOURCES",
    "PASSWORD",
    "RESOURCES",
    "SANDBOX_CLI",
    "SANDBOX_RUNNER",
    "TEMPLATE_MAX_BYTES",
    "VAPID_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "VAPID_SUBJECT",
    "WEB_DIST",
)
"""The settings an operator, compose or a script sets by name (README, Settings)."""


def setting(suffix: str, default: str = "", env: Mapping[str, str] = os.environ) -> str:
    """`KRIFLOW_<suffix>`, else `SWARMIFY_<suffix>`, else `default`; an empty value counts as
    unset."""
    return env.get(NEW + suffix) or env.get(OLD + suffix) or default


def old_settings(env: Mapping[str, str] = os.environ) -> list[str]:
    """The old names of operator settings set in `env`, to rename in `.env`."""
    return [OLD + s for s in OPERATOR if env.get(OLD + s)]


def old_settings_line(env: Mapping[str, str] = os.environ) -> str | None:
    """The startup log line naming `old_settings`, or None when there are none."""
    if old := old_settings(env):
        return (
            f"Read old settings: {', '.join(old)}. Rename them to KRIFLOW_* in .env;"
            " the old names stop working in a later release."
        )
    return None


def data_dir(env: Mapping[str, str] = os.environ, home: Path | None = None) -> Path:
    """`KRIFLOW_DATA_DIR` (or the old name), else `~/.kriflow`, except that an existing
    `~/.swarmify` is used while `~/.kriflow` does not exist. Nothing is moved."""
    if given := setting("DATA_DIR", env=env):
        return Path(given)
    home = home or Path.home()
    new, old = home / ".kriflow", home / ".swarmify"
    return old if not new.exists() and old.exists() else new


def both(env: Mapping[str, str]) -> dict[str, str]:
    """`env` with each `SWARMIFY_<X>` also as `KRIFLOW_<X>`: what the core hands a package
    process (kriflow#595). Permanent: installed package versions stay pinned and read the old
    names, so both are always written."""
    out = dict(env)
    for name, value in env.items():
        if name.startswith(OLD):
            out[NEW + name.removeprefix(OLD)] = value
    return out
