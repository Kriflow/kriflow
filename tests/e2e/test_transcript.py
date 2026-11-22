"""An agent stage's Transcript (issue 533), at phone size first: Summary by default, then the
agents' own words with their faces, tool calls as rows that open to their input and result, a
write as a diff, and a filter to one agent. Nothing runs wide and every tap is 44 px. Set
SWARMIFY_SHOTS to a folder to keep screenshots."""

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect
from test_phone_audit import CHECKS

PHONES: list[ViewportSize] = [{"width": 390, "height": 844}, {"width": 360, "height": 740}]
RESEARCH = """name: Research
stages:
  - swarm: research
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
    output_type: list[idea]
"""
SHOTS = os.environ.get("SWARMIFY_SHOTS")


@pytest.fixture(scope="module")
def ids(base_url: str) -> dict[str, str]:
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    world = api.post("/worlds", json={"name": "Transcript"}).json()["id"]
    wf = api.post(f"/worlds/{world}/workflows", json={"yaml": RESEARCH}).json()["workflow_id"]
    run = api.post(f"/workflows/{wf}/runs", json={}).json()["id"]
    for _ in range(300):
        if api.get(f"/runs/{run}").json()["status"] == "done":
            return {"world": world, "run": run}
        time.sleep(0.1)
    raise AssertionError(f"{run} never finished")


@pytest.fixture(params=PHONES, ids=lambda v: f"{v['width']}")
def phone(request: pytest.FixtureRequest, browser: Browser, base_url: str) -> Iterator[Page]:
    context = browser.new_context(
        viewport=request.param, base_url=base_url, is_mobile=True, has_touch=True
    )
    yield context.new_page()
    context.close()


def _audit(page: Page) -> None:
    """The phone audit's checks: nothing wider than the screen, every tap 44 px, nothing over
    the tab bar."""
    found = page.evaluate(CHECKS, page.viewport_size["width"])  # type: ignore[index]
    assert found == []


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=f"{SHOTS}/phone-{page.viewport_size['width']}-{name}.png")  # type: ignore[index]


def _research(page: Page, ids: dict[str, str]) -> None:
    page.goto(f"/worlds/{ids['world']}/runs/{ids['run']}")
    page.get_by_test_id("timeline").get_by_role("button", name=re.compile(r"^Step 1, ")).click()
    expect(page.get_by_test_id("stage-panel").get_by_role("tab", name="Activity")).to_be_visible()


def test_a_phone_reads_the_summary_first_then_the_transcript(
    phone: Page, ids: dict[str, str]
) -> None:
    _research(phone, ids)
    card = phone.get_by_test_id("stage-panel")
    summary = card.get_by_role("button", name="Summary")
    expect(summary).to_have_attribute("aria-pressed", "true")
    expect(card.get_by_test_id("log")).to_be_visible()
    expect(card.get_by_test_id("transcript")).to_be_hidden()
    _audit(phone)

    card.get_by_role("button", name="Transcript").click()
    tx = card.get_by_test_id("transcript")
    expect(tx).to_be_visible()
    expect(card.get_by_test_id("log")).to_be_hidden()
    expect(tx).to_contain_text("Worker 1")
    expect(tx).to_contain_text("I'll check what the world already knows, then claim the task.")

    graph = tx.get_by_test_id("tool-row").filter(has_text="query_graph").first
    expect(graph).to_contain_text("What do we know already?")
    expect(graph.locator(".tool-out")).to_be_hidden()
    graph.locator("summary").click()
    expect(graph.locator(".tool-out")).to_be_visible()

    write = tx.get_by_test_id("tool-row").filter(has_text="write_artifact")
    expect(write).to_contain_text(re.compile(r"ideas\.md\s*\+\d+ lines"))
    write.locator("summary").click()
    expect(write.get_by_test_id("diff")).to_contain_text("+ # Ideas")
    write.scroll_into_view_if_needed()
    _audit(phone)
    _shot(phone, "transcript")

    phone.get_by_test_id("transcript-agent").select_option(label="Worker 1")
    expect(tx).not_to_contain_text("Planner:")
    expect(tx).not_to_contain_text("Judge:")
    expect(tx.get_by_test_id("tool-row").first).to_be_visible()
    _audit(phone)
    card.get_by_role("button", name="Transcript").scroll_into_view_if_needed()
    _shot(phone, "transcript-one-agent")


def test_a_desktop_opens_on_the_transcript(browser: Browser, base_url: str, ids: dict) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, base_url=base_url)
    page = context.new_page()
    _research(page, ids)
    card = page.get_by_test_id("stage-panel")
    expect(card.get_by_role("button", name="Transcript")).to_have_attribute("aria-pressed", "true")
    expect(card.get_by_test_id("transcript")).to_be_visible()
    card.get_by_test_id("tool-row").filter(has_text="write_artifact").locator("summary").click()
    wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 0
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/desktop-transcript.png", full_page=True)
    context.close()
