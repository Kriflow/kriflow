"""Shared helpers for the API tests: a polling wait, the recording Notifier fake, and the
fake_publish fixture package's own preview and outcomes with its account signed in, for tests
that publish through it over the fake ConnectorTransport."""

import importlib.util
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kriflow_connectors.setup import SetupStore
from kriflow_kernel import Event

FAKE_PUBLISH = (
    Path(__file__).parents[3] / "packages/connectors/tests/fixtures/packages/fake_publish"
)
_spec = importlib.util.spec_from_file_location("fake_publish", FAKE_PUBLISH / "fake_publish.py")
assert _spec and _spec.loader
fake_publish = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_publish)
FAKE_PUBLISH_ENV = fake_publish.TOKEN
"""What fake_publish's process gets once `sign_in_fake_publish` has run."""


def fake_preview(args: dict[str, Any]) -> dict[str, Any]:
    """fake_publish's `preview`, answering as its tool would."""
    return fake_publish.preview_of(args)


def fake_outcomes(views: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """fake_publish's `outcomes`: each of its posts at `views`."""
    return lambda args: {post: {"views": views} for post in fake_publish.claimed(args["posts"])}


def sign_in_fake_publish(data_dir: Path) -> None:
    """fake_publish's account signed in, as a sign-in on the Connectors page leaves it."""
    store = SetupStore(data_dir / "setup.json")
    store.put_value("fake_publish", None, "account_client_id", "id")
    store.put_value("fake_publish", None, "account_client_secret", "s")
    tokens = {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 3600}
    store.put_value("fake_publish", None, "account", tokens)


def until(check: Callable[[], bool], seconds: float = 30) -> None:
    """Poll `check` until it holds. Wait on the state the next step needs, not a status that
    can still be the old one. A run's wall time grows with the machine's load (a fan-out that
    takes 2.7 s alone took 9.6 s at -n 16, issue 601), so the limit only catches a hang."""
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def studio(client: Any, world_id: str) -> None:
    """Add `studio` to the world: a stage connector that requires nothing, standing in for any
    in tests that replace the connector stage call, so it is never started."""
    body = {
        "name": "studio",
        "kind": "stage",
        "server": "uvx studio-mcp",
        "stage_types": ["connector"],
    }
    assert client.post(f"/api/worlds/{world_id}/connectors", json=body).status_code == 201


class FakeNotifier:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def notify(self, event: Event) -> None:
        self.events.append(event)


def install(client: Any, source: str) -> Any:
    """Install a package over HTTP as the Connectors page does: review it, then approve every
    permission it declares. The review's answer when it refuses."""
    review = client.post("/api/packages/review", json={"source": source})
    if review.status_code != 200:
        return review
    approved = review.json()["permissions"]
    return client.post("/api/packages", json={"source": source, "approved": approved})
