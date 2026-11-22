"""The run page's status hero and stage rail at phone size (issue 531): one status line with its
one action, four numbers each with its anchor, and every stage an icon tile that fits the screen,
scrolls sideways and selects its stage on a tap. Set SWARMIFY_SHOTS to a folder to keep
screenshots of each state."""

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from scripted_app import LONG

PHONES: list[ViewportSize] = [{"width": 390, "height": 844}, {"width": 360, "height": 740}]
FLOW = """name: Brief then research
stages:
  - swarm: research
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
    tools: [web_search]
  - gate: pick
    ask: Which idea do we shoot?
"""
RENDER = """name: Research then render
stages:
  - swarm: research
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
    output_type: list[idea]
    produces: [ideas.md]
  - connector: render
    calls: remotion_render
    input: ideas.md
    output: short.mp4
    params: {project: shorts, composition: Short}
"""
CHILD = "name: Brief\nstages:\n  - gate: brief\n    ask: Go?\n"
FAN = """name: Fan out
stages:
  - swarm: research
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
    output_type: list[idea]
  - gate: choose
    kind: pick
    picks: 2
    ask: Pick two.
  - workflow: each
    runs: {child}
    for_each: choose
"""
SHOTS = os.environ.get("SWARMIFY_SHOTS")


@pytest.fixture(scope="module")
def ids(base_url: str) -> dict[str, str]:
    """Runs waiting at a gate, done twice over (so the second has a last run), and a fan-out
    running over two picked items."""
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    world = api.post("/worlds", json={"name": "Hero"}).json()["id"]
    api.patch(f"/worlds/{world}", json={"run_cap": 8}).raise_for_status()

    def make(yaml: str) -> str:
        made = api.post(f"/worlds/{world}/workflows", json={"yaml": yaml})
        assert made.status_code == 201, made.text
        return made.json()["workflow_id"]

    def start(workflow: str, status: str) -> str:
        run = api.post(f"/workflows/{workflow}/runs", json={}).json()["id"]
        return until(run, status)

    def until(run: str, status: str) -> str:
        for _ in range(300):
            if api.get(f"/runs/{run}").json()["status"] == status:
                return run
            time.sleep(0.1)
        raise AssertionError(f"{run} never reached {status}")

    render = make(RENDER)
    start(render, "done")
    done = start(render, "done")
    fan = start(make(FAN.format(child=make(CHILD))), "waiting_gate")
    research = api.get(f"/runs/{fan}").json()["stages"][0]["id"]
    cards = [
        a["path"]
        for a in api.get(f"/runs/{fan}/artifacts").json()
        if a["stage"] == research and a["type"] == "idea"
    ]
    api.post(f"/runs/{fan}/decision", json={"artifacts": cards[:2]}).raise_for_status()
    until(fan, "running")
    return {
        "world": world,
        "waiting": start(make(FLOW), "waiting_gate"),
        "long": start(make(LONG), "waiting_gate"),
        "done": done,
        "fan": fan,
    }


@pytest.fixture(params=PHONES, ids=lambda v: f"{v['width']}")
def phone(request: pytest.FixtureRequest, browser: Browser, base_url: str) -> Iterator[Page]:
    context = browser.new_context(
        viewport=request.param, base_url=base_url, is_mobile=True, has_touch=True
    )
    yield context.new_page()
    context.close()


def _fits(page: Page) -> None:
    wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert wide <= 0, f"{page.url} scrolls sideways by {wide}px"


def _taps(page: Page) -> None:
    """Every rail tile and hero action is a 44 px tap."""
    small = page.evaluate(
        """[...document.querySelectorAll('.rail button, .hero-go > *')]
          .map(e => e.getBoundingClientRect())
          .filter(r => Math.min(r.width, r.height) < 43.5).length"""
    )
    assert small == 0


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        width = page.viewport_size["width"]  # type: ignore[index]
        page.screenshot(path=f"{SHOTS}/phone-{width}-{name}.png")


def test_waiting_at_a_gate_the_hero_asks_and_the_rail_marks_the_gate(
    phone: Page, ids: dict[str, str]
) -> None:
    phone.goto(f"/worlds/{ids['world']}/runs/{ids['waiting']}")
    hero = phone.get_by_test_id("hero")
    expect(hero.get_by_test_id("status-line")).to_have_text(
        "Waiting for you: Which idea do we shoot?"
    )
    expect(hero.get_by_test_id("hero-sub")).to_have_text(
        re.compile(r"^pick, step 2 of 3\. Waiting")
    )
    expect(hero.get_by_test_id("steps-stat")).to_contain_text("1 of 3")
    rail = phone.get_by_test_id("timeline")
    gate = rail.get_by_role("button", name=re.compile(r"^Step 2, pick"))
    expect(gate).to_have_attribute("aria-current", "step")
    expect(gate).to_have_attribute("aria-pressed", "true")
    expect(rail.locator("li").first.locator(".mark-done")).to_be_visible()
    expect(rail.locator(".node-waiting .dt")).to_have_text(re.compile(r"^waiting "))
    _fits(phone)
    _taps(phone)
    _shot(phone, "waiting")


def test_a_long_run_rail_scrolls_and_a_tap_pins_the_stage(phone: Page, ids: dict[str, str]) -> None:
    phone.goto(f"/worlds/{ids['world']}/runs/{ids['long']}")
    rail = phone.get_by_test_id("timeline")
    expect(rail.locator("li")).to_have_count(10)  # and the retro
    assert rail.evaluate("e => e.scrollWidth > e.clientWidth")
    last = rail.get_by_role("button", name=re.compile(r"^Step 10,"))
    last.click()
    expect(phone).to_have_url(re.compile(r"\?stage="))
    expect(last).to_have_attribute("aria-pressed", "true")
    box, frame = last.bounding_box(), rail.bounding_box()
    assert (
        box
        and frame
        and box["x"] >= frame["x"]
        and box["x"] + box["width"] <= frame["x"] + frame["width"]
    )
    # Reloaded, the pinned stage is still selected and scrolled into the rail's middle.
    phone.reload()
    expect(last).to_have_attribute("aria-pressed", "true")
    assert rail.evaluate("e => e.scrollLeft") > 0
    _fits(phone)
    _taps(phone)


def test_done_the_hero_names_the_output_and_the_last_run(phone: Page, ids: dict[str, str]) -> None:
    phone.goto(f"/worlds/{ids['world']}/runs/{ids['done']}")
    hero = phone.get_by_test_id("hero")
    expect(hero.get_by_test_id("status-line")).to_have_text(
        re.compile(r"^Done in .*\. Made short\.mp4\.$")
    )
    expect(hero.get_by_test_id("last-run")).to_have_text(re.compile(r"^took, last run \d"))
    expect(hero.get_by_role("link", name="Open short.mp4")).to_be_visible()
    # The connector stage wears its package's own icon, served by the API.
    icon = phone.get_by_test_id("timeline").get_by_test_id("package-icon")
    expect(icon).to_be_visible()
    assert icon.evaluate("e => e.complete && e.naturalWidth > 0")
    _fits(phone)
    _taps(phone)
    _shot(phone, "done")


def test_a_running_fan_out_shows_its_count_and_its_progress(
    phone: Page, ids: dict[str, str]
) -> None:
    phone.goto(f"/worlds/{ids['world']}/runs/{ids['fan']}")
    hero = phone.get_by_test_id("hero")
    expect(hero.get_by_test_id("status-line")).to_have_text(
        "Each is working: 0 of 2 done · step 3 of 4"
    )
    expect(hero.get_by_role("button", name="Pause")).to_be_visible()
    fan = phone.get_by_test_id("timeline").locator("li").nth(2)
    expect(fan).to_have_class(re.compile("node-running"))
    expect(fan.locator(".count")).to_have_text("×2")
    expect(fan.locator(".dt")).to_have_text("0 of 2")
    _fits(phone)
    _taps(phone)
    _shot(phone, "running")


def test_on_a_desktop_the_numbers_sit_in_one_row_with_their_anchors(
    browser: Browser, base_url: str, ids: dict[str, str]
) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, base_url=base_url)
    page = context.new_page()
    page.goto(f"/worlds/{ids['world']}/runs/{ids['fan']}")
    stats = page.get_by_test_id("hero").locator(".stat")
    expect(stats).to_have_count(4)
    tops = {round(b["y"]) for b in (stats.nth(i).bounding_box() for i in range(4)) if b}
    assert len(tops) == 1, tops
    expect(page.get_by_test_id("agents-stat")).to_contain_text(re.compile(r"working now|worked on"))
    expect(page.get_by_test_id("spend-stat").locator(".mini")).to_be_visible()
    panel, side = (
        page.get_by_test_id("stage-panel").bounding_box(),
        page.locator(".run-side").bounding_box(),
    )
    assert (
        panel and side and side["x"] > panel["x"] + panel["width"] - 1
    )  # the side column on the right
    _fits(page)
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/desktop-running.png")
    context.close()
