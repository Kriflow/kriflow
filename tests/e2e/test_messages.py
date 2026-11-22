"""Messaging a running stage (issue 534), at phone size first: the message box docks above the tab
bar with its To chips, a message reaches the live session and its receipt shows under it in the
Transcript; nothing runs wide and every tap is 44 px. On a desktop the box sits at the foot of
the stage card. The scripted `steer` worker waits mid-turn for a message. Set SWARMIFY_SHOTS to a
folder to keep screenshots."""

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
STEER = """name: Steer
stages:
  - swarm: steer
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
    output_type: list[idea]
"""
SHOTS = os.environ.get("SWARMIFY_SHOTS")
WAITING = "I'll start once you say which sources to trust."


@pytest.fixture
def live(base_url: str) -> dict[str, str]:
    """A run whose `steer` stage has a worker mid-turn, waiting for a message."""
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    world = api.post("/worlds", json={"name": "Messages"}).json()["id"]
    wf = api.post(f"/worlds/{world}/workflows", json={"yaml": STEER}).json()["workflow_id"]
    run = api.post(f"/workflows/{wf}/runs", json={}).json()
    stage = run["stages"][0]["id"]
    for _ in range(300):
        if any(
            e["detail"] == WAITING for e in api.get(f"/runs/{run['id']}/stages/{stage}/log").json()
        ):
            return {"world": world, "run": run["id"], "stage": stage}
        time.sleep(0.1)
    raise AssertionError(f"{run['id']} never got to its worker")


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


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=f"{SHOTS}/phone-{page.viewport_size['width']}-{name}.png")  # type: ignore[index]


def _open(page: Page, ids: dict[str, str]) -> None:
    page.goto(f"/worlds/{ids['world']}/runs/{ids['run']}")
    page.get_by_test_id("timeline").get_by_role("button", name=re.compile(r"^Step 1, ")).click()


def test_a_phone_messages_the_running_stage_from_the_dock(phone: Page, live: dict) -> None:
    _open(phone, live)
    box = phone.get_by_test_id("message-box")
    expect(box).to_be_visible()
    everyone = box.get_by_role("button", name="Everyone working (1)")
    expect(everyone).to_have_attribute("aria-pressed", "true")
    expect(box.get_by_role("button", name=re.compile("Worker 1"))).to_be_visible()
    # Docked on the tab bar: its foot is the tab bar's top.
    dock, bar = box.bounding_box(), phone.locator(".tabbar").bounding_box()
    assert dock and bar and abs(dock["y"] + dock["height"] - bar["y"]) <= 1
    card = phone.get_by_test_id("stage-panel")
    card.get_by_role("button", name="Transcript").click()
    expect(card.get_by_test_id("transcript")).to_contain_text(WAITING)
    _audit(phone)
    box.get_by_role("textbox").fill("Use the filing, not the blog post.")
    expect(box.get_by_role("button", name="Send")).to_be_enabled()
    phone.wait_for_timeout(300)  # the Send button's colour eases in
    _shot(phone, "composer")

    box.get_by_role("button", name="Send").click()
    message = card.get_by_test_id("message")
    expect(message).to_contain_text("You to everyone still working")
    expect(message).to_contain_text("Use the filing, not the blog post.")
    expect(message.get_by_test_id("receipt")).to_have_text(
        "Delivered to Worker 1, saved as a run change", timeout=10_000
    )
    expect(card.get_by_test_id("transcript")).to_contain_text("Got it. Using the filing")
    message.scroll_into_view_if_needed()
    _audit(phone)
    _shot(phone, "receipt")
    # The stage ends; there is no one left to message.
    expect(box).to_be_hidden(timeout=15_000)
    _audit(phone)


def test_a_desktop_stops_the_turn_from_the_foot_of_the_card(
    browser: Browser, base_url: str, live: dict
) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, base_url=base_url)
    page = context.new_page()
    _open(page, live)
    card = page.get_by_test_id("stage-panel")
    box = card.get_by_test_id("message-box")
    expect(box).to_contain_text("They read it at their next turn.")
    box.get_by_role("button", name=re.compile("Worker 1")).click()
    box.get_by_role("textbox").fill("Use the filing, not the blog post.")
    page.wait_for_timeout(300)  # the Send button's colour eases in
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/desktop-composer.png", full_page=True)
    box.get_by_role("button", name="Stop and do this instead").click()

    message = card.get_by_test_id("message")
    expect(message).to_contain_text("You to Worker 1, stop and do this instead")
    expect(message.get_by_test_id("receipt")).to_have_text(
        "Delivered to Worker 1, saved as a run change", timeout=10_000
    )
    expect(card.get_by_test_id("transcript")).to_contain_text("Got it. Using the filing")
    expect(card.get_by_test_id("transcript")).not_to_contain_text("What do we know already?")
    wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 0
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/desktop-receipt.png", full_page=True)
    context.close()
