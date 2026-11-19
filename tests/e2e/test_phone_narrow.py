"""The pages most likely to run wide, at the narrowest common phone (360 x 740), and Needs you
holding still while it loads: what it shows first is where it stays (Mobile UX Spec, T6)."""

import shlex
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

# The phone suite's fixtures: its seeded world, and a signed-in 390 x 844 page.
from test_phone import PASSWORD, signed_in
from test_phone import ids as ids  # noqa: F401
from test_phone import phone as phone  # noqa: F401

SMALL: ViewportSize = {"width": 360, "height": 740}
CLS = """window.__cls = 0;
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) if (!e.hadRecentInput) window.__cls += e.value;
}).observe({ type: "layout-shift", buffered: true });"""


@pytest.fixture
def small(browser: Browser, locked_url: str) -> Iterator[Page]:
    context = browser.new_context(
        viewport=SMALL, base_url=locked_url, is_mobile=True, has_touch=True
    )
    context.request.post("/api/session", data={"password": PASSWORD})
    yield context.new_page()
    context.close()


ECHO = Path(__file__).parents[2] / "packages/connectors/tests/echo_server.py"
# A Tool connector whose command is one long unbreakable path, as an MCP server added on the
# Connectors page shows it (the Connectors page once ran 250px too wide on a phone with one).
LONG = (
    "/data/worlds/world_01M65QPFJ3QWXWQWQG4NKP96HG/assets/" + "a_long_folder_name_" * 4 + "tool.py"
)


@pytest.fixture(scope="module")
def long_command(locked_url: str, ids: dict[str, str]) -> None:
    server = shlex.join([sys.executable, str(ECHO), LONG])
    body = {"name": "long_path", "kind": "tool", "server": server, "stage_types": ["swarm"]}
    api = signed_in(locked_url)
    api.post(f"/worlds/{ids['world']}/connectors", json=body).raise_for_status()
    api.close()


@pytest.mark.parametrize("path", ["graph", "cast", "assets", "connectors"])
def test_fits_a_small_phone(
    small: Page, ids: dict[str, str], long_command: None, path: str
) -> None:
    small.goto(f"/worlds/{ids['world']}/{path}")
    expect(small.locator("h1").first).to_be_visible()
    expect(small.locator("[aria-busy='true']")).to_have_count(0)
    assert small.evaluate("window.innerWidth") == SMALL["width"]
    wide = small.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    culprits = small.evaluate(
        """[...document.querySelectorAll('body *')]
            .filter(e => e.getBoundingClientRect().right > window.innerWidth + 1)
            .slice(0, 5).map(e => e.tagName + '.' + e.className)"""
    )
    assert wide <= 0, f"{path} scrolls sideways by {wide}px: {culprits}"


def test_needs_you_holds_still_while_it_loads(phone: Page, ids: dict[str, str]) -> None:
    """Cumulative layout shift under 0.1 from first paint to the open decision."""
    phone.add_init_script(CLS)
    phone.goto("/gates")
    expect(phone.locator(".gate").first).to_be_visible()
    expect(phone.locator("[aria-busy='true']")).to_have_count(0)
    phone.wait_for_timeout(1000)  # the evidence and the moving runs settle
    cls = phone.evaluate("window.__cls")
    assert cls < 0.1, f"Needs you shifts by {cls:.3f}"
