import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
import uvicorn
from api_fakes import fake_preview
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fake_graph import fake_extract
from swarmify_api import main
from swarmify_api.main import create_app
from swarmify_connectors import Connectors, FakeTransport, Packages
from swarmify_connectors.fake import FIXTURE_DEFAULTS
from swarmify_memory import FakeGraphIndex
from swarmify_swarm import SdkRuntime
from swarmify_swarm.fake import Script
from swarmify_swarm.network import NetworkWait


@pytest.fixture(autouse=True)
def fixture_defaults(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's default packages are the fixture ones (FIXTURE_DEFAULTS), never the real
    packages, and their install steps are skipped: the API starts the app ~200 times. A module
    that needs an install step to run sets `real_default_installs = True`."""
    real = Connectors.install_defaults
    skip = not getattr(request.module, "real_default_installs", False)

    def install_defaults(self: Connectors, sources: Sequence[str] | None = None) -> dict[str, str]:
        with monkeypatch.context() as m:
            if skip:
                m.setattr(Packages, "_run_install", lambda *a: None)
            return real(self, FIXTURE_DEFAULTS if sources is None else sources)

    monkeypatch.setattr(Connectors, "install_defaults", install_defaults)


@pytest.fixture(autouse=True)
def no_prepare_commands(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Template's prepare command (kit's `prepare.py`) runs for real only in a module
    that sets `real_prepare = True`; elsewhere every prepare succeeds at once."""
    if not getattr(request.module, "real_prepare", False):
        monkeypatch.setattr(Packages, "prepare", lambda *a: None)


@pytest.fixture
def transport() -> FakeTransport:
    """Every package's `preview` answers as fake_publish's own does; a test of another publish
    connector sets its own."""
    return FakeTransport(outputs={"preview": [fake_preview]})


@pytest.fixture
def script() -> Script:
    """What the scripted CLI does in each agent session; a test replaces `scripts`."""
    return Script()


@pytest.fixture(autouse=True)
def no_drain_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """An app stopped with a stage still at work stops at once, as before the Drain; a drain
    test sets its own limit."""
    monkeypatch.setenv("SWARMIFY_DRAIN_SECONDS", "0")


@pytest.fixture(autouse=True)
def no_web_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A built apps/web/dist in the checkout would serve every unknown path (a PUT to one is 405,
    not 404); tests that want the web skin set WEB_DIST themselves."""
    monkeypatch.setattr(main, "WEB_DIST", tmp_path / "no-web-dist")


@pytest.fixture
def graph_index() -> FakeGraphIndex:
    return FakeGraphIndex()


@pytest.fixture
def app(
    tmp_path: Path, transport: FakeTransport, script: Script, graph_index: FakeGraphIndex
) -> FastAPI:
    """The API with the fake ConnectorTransport, a scripted agent CLI, the fake GraphIndex and
    a GraphWriter whose extract writes a fixed graph. The network is back as soon as it is
    probed."""
    return create_app(
        tmp_path,
        transport=transport,
        poll_seconds=0.01,
        runtime=SdkRuntime(transport=script.transport),
        graph_index=graph_index,
        extract=fake_extract,
        retro=False,
        network=NetworkWait(probe=lambda: True, sleep=lambda _: None),  # back at once
    )


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def live_url(app: FastAPI) -> Iterator[str]:
    """A real uvicorn server in a thread. TestClient buffers bodies, so SSE needs this."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
