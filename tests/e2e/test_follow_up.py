"""Follow-ups and Attempts (issue 535), at phone size first: on a finished agent stage the dock
offers Ask for a change and Rerun from scratch, naming the later stages that run again and the
gate that waits again before you send. A Follow-up continues the stage as Attempt 2, a tab with
its time and spend, your words pinned on it. Nothing runs wide and every tap is 44 px. On a
desktop the box sits at the foot of the stage card, Rerun from scratch beside Ask for a change.
Set KRIFLOW_SHOTS to a folder to keep screenshots."""

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
DRAFT = """name: Draft
stages:
  - swarm: draft
    goal: Find 2 ideas.
    done_when: 2 ideas ranked
    output_type: list[idea]
  - gate: review
    ask: Read them. Approve or send back.
    kind: approve
"""
SHOTS = os.environ.get("KRIFLOW_SHOTS")
WORDS = "Drop the second idea and add sources to the first."


@pytest.fixture
def waiting(request: pytest.FixtureRequest, base_url: str) -> dict[str, str]:
    """A run whose `draft` stage is done and whose gate waits."""
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    world = api.post("/worlds", json={"name": "Follow-ups"}).json()["id"]
    request.addfinalizer(lambda: api.delete(f"/worlds/{world}"))
    wf = api.post(f"/worlds/{world}/workflows", json={"yaml": DRAFT}).json()["workflow_id"]
    run = api.post(f"/workflows/{wf}/runs", json={}).json()
    for _ in range(300):
        if api.get(f"/runs/{run['id']}").json()["status"] == "waiting_gate":
            return {"world": world, "run": run["id"]}
        time.sleep(0.1)
    raise AssertionError(f"{run['id']} never got to its gate")


@pytest.fixture(params=PHONES, ids=lambda v: f"{v['width']}")
def phone(request: pytest.FixtureRequest, browser: Browser, base_url: str) -> Iterator[Page]:
    context = browser.new_context(
        viewport=request.param, base_url=base_url, is_mobile=True, has_touch=True
    )
    yield context.new_page()
    context.close()


def _audit(page: Page) -> None:
    found = page.evaluate(CHECKS, page.viewport_size["width"])  # type: ignore[index]
    assert found == []


def _shot(page: Page, name: str, full: bool = False) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        w = page.viewport_size["width"]  # type: ignore[index]
        page.screenshot(
            path=f"{SHOTS}/{'desktop' if w > 800 else f'phone-{w}'}-{name}.png", full_page=full
        )


def _open(page: Page, ids: dict[str, str]) -> None:
    page.goto(f"/worlds/{ids['world']}/runs/{ids['run']}")
    page.get_by_test_id("timeline").get_by_role("button", name=re.compile(r"^Step 1, ")).click()


def test_a_phone_asks_a_finished_stage_for_a_change_as_attempt_2(
    phone: Page, waiting: dict
) -> None:
    _open(phone, waiting)
    box = phone.get_by_test_id("follow-up-box")
    expect(box).to_be_visible()
    expect(box.get_by_test_id("rerun-warning")).to_have_text(
        "A change here reruns review. review waits for you again."
    )
    # Docked on the tab bar, once it has risen: its foot is the tab bar's top.
    phone.wait_for_timeout(400)
    dock, bar = box.bounding_box(), phone.locator(".tabbar").bounding_box()
    assert dock and bar and abs(dock["y"] + dock["height"] - bar["y"]) <= 1
    expect(box.get_by_role("button", name="Send")).to_be_disabled()
    rerun = box.get_by_role("button", name="Rerun from scratch")
    expect(rerun).to_have_attribute("aria-pressed", "false")
    _audit(phone)
    box.get_by_role("textbox").fill(WORDS)
    phone.wait_for_timeout(300)  # the Send button's colour eases in
    _shot(phone, "dock")

    box.get_by_role("button", name="Send").click()
    card = phone.get_by_test_id("stage-panel")
    tabs = card.get_by_test_id("attempts")
    expect(tabs.get_by_role("button", name=re.compile("^Attempt 2"))).to_contain_text(
        "your change", timeout=15_000
    )
    pin = card.get_by_test_id("attempt-pin")
    expect(pin).to_contain_text(f"“{WORDS}”")
    expect(pin).to_contain_text("Continues attempt 1's session")
    # It ran again and the gate waits again; the box is back for another change.
    expect(card.get_by_test_id("stage-meta")).to_contain_text("2 attempts", timeout=15_000)
    expect(tabs.get_by_role("button", name=re.compile("^Attempt 2"))).to_contain_text(
        re.compile(r"\$0\.\d\d")
    )
    expect(box).to_be_visible()
    phone.wait_for_timeout(400)  # the dock rises again
    _audit(phone)
    card.evaluate("e => e.scrollIntoView({block: 'start'})")
    _shot(phone, "attempts")

    tabs.get_by_role("button", name=re.compile("^Attempt 1")).click()
    expect(tabs.get_by_role("button", name=re.compile("^Attempt 1"))).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(pin).to_be_hidden()
    card.get_by_role("tab", name=re.compile("^Files")).click()
    expect(card.get_by_test_id("tab-files")).to_contain_text("ideas.md")
    _audit(phone)
    _shot(phone, "attempt-1")


def test_a_desktop_reruns_from_scratch_from_the_foot_of_the_card(
    browser: Browser, base_url: str, waiting: dict
) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, base_url=base_url)
    page = context.new_page()
    _open(page, waiting)
    card = page.get_by_test_id("stage-panel")
    box = card.get_by_test_id("follow-up-box")
    expect(box).to_contain_text("Keeps the session and what it read. Rerun starts clean.")
    box.get_by_role("button", name="Rerun from scratch").click()
    expect(box.get_by_role("button", name="Rerun", exact=True)).to_be_enabled()
    box.get_by_role("textbox").fill("Start over with fewer ideas.")
    _shot(page, "follow-up-box", full=True)
    box.get_by_role("button", name="Rerun", exact=True).click()

    pin = card.get_by_test_id("attempt-pin")
    expect(pin).to_contain_text("“Start over with fewer ideas.”", timeout=15_000)
    expect(pin).to_contain_text("Started clean")
    expect(card.get_by_test_id("stage-meta")).to_contain_text("2 attempts", timeout=15_000)
    wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 0
    _shot(page, "attempts", full=True)
    context.close()
