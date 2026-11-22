"""The composed app under either settings name (kriflow#594): an install whose .env still says
SWARMIFY_* signs in, serves its VAPID key and counts its Resources as before, and the startup
log names the old settings; the KRIFLOW_* names do the same and win when both are set."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kriflow_api import main
from kriflow_connectors import resources
from kriflow_notifications.push import generate_keys

OLD_PUBLIC, OLD_PRIVATE = generate_keys()
NEW_PUBLIC, NEW_PRIVATE = generate_keys()


def settings(prefix: str, password: str, public: str, private: str) -> dict[str, str]:
    return {
        f"{prefix}PASSWORD": password,
        f"{prefix}VAPID_PUBLIC_KEY": public,
        f"{prefix}VAPID_PRIVATE_KEY": private,
        f"{prefix}RESOURCES": "seat=3",
    }


@pytest.mark.parametrize(
    ("env", "password", "public", "logged"),
    [
        (settings("SWARMIFY_", "old", OLD_PUBLIC, OLD_PRIVATE), "old", OLD_PUBLIC, True),
        (settings("KRIFLOW_", "new", NEW_PUBLIC, NEW_PRIVATE), "new", NEW_PUBLIC, False),
        (
            settings("SWARMIFY_", "old", OLD_PUBLIC, OLD_PRIVATE)
            | settings("KRIFLOW_", "new", NEW_PUBLIC, NEW_PRIVATE),
            "new",
            NEW_PUBLIC,
            True,
        ),
    ],
    ids=["old", "new", "both"],
)
def test_either_name_configures_the_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env: dict[str, str],
    password: str,
    public: str,
    logged: bool,
) -> None:
    for prefix in ("KRIFLOW_", "SWARMIFY_"):
        for name in ("PASSWORD", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "RESOURCES"):
            monkeypatch.delenv(prefix + name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with caplog.at_level(logging.WARNING), TestClient(main.create_app(tmp_path)) as client:
        assert client.post("/api/session", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/session", json={"password": password}).status_code == 204
        assert client.get("/api/push/key").json()["public_key"] == public
    assert resources.counts()["seat"] == 3
    line = [r.message for r in caplog.records if r.message.startswith("Read old settings")]
    assert bool(line) == logged
    if logged:
        assert "SWARMIFY_PASSWORD" in line[0] and "SWARMIFY_VAPID_PUBLIC_KEY" in line[0]
