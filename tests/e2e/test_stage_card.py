"""The run page's open stage card and side column (issue 532), at phone size first: tabs fitted to
the stage's kind, a pick gate's cards with their scored chips, the gate's answer bar pinned over
the tab bar whichever tab is open, and under the card what the run made and what each stage
spent. Set SWARMIFY_SHOTS to a folder to keep screenshots of each state."""

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

PHONES: list[ViewportSize] = [{"width": 390, "height": 844}, {"width": 360, "height": 740}]
PICK = """name: Research then pick
stages:
  - swarm: research
    goal: Find 5 ideas.
    done_when: 5 ideas ranked
    output_type: list[idea]
  - gate: choose
    kind: pick
    ask: Which one goes on?
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
SHOTS = os.environ.get("SWARMIFY_SHOTS")


@pytest.fixture(scope="module")
def ids(base_url: str) -> dict[str, str]:
    """A pick workflow to start runs of, and a done run."""
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    world = api.post("/worlds", json={"name": "Card"}).json()["id"]
    api.patch(f"/worlds/{world}", json={"run_cap": 8}).raise_for_status()

    def start(yaml: str, status: str) -> str:
        made = api.post(f"/worlds/{world}/workflows", json={"yaml": yaml})
        assert made.status_code == 201, made.text
        run = api.post(f"/workflows/{made.json()['workflow_id']}/runs", json={}).json()["id"]
        for _ in range(300):
            if api.get(f"/runs/{run}").json()["status"] == status:
                return run
            time.sleep(0.1)
        raise AssertionError(f"{run} never reached {status}")

    made = api.post(f"/worlds/{world}/workflows", json={"yaml": PICK}).json()["workflow_id"]
    return {"world": world, "pick": made, "done": start(RENDER, "done")}


@pytest.fixture
def pick(ids: dict[str, str], base_url: str) -> str:
    """A fresh run waiting at its pick gate, since each phone size answers one."""
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    run = api.post(f"/workflows/{ids['pick']}/runs", json={}).json()["id"]
    for _ in range(300):
        if api.get(f"/runs/{run}").json()["status"] == "waiting_gate":
            return run
        time.sleep(0.1)
    raise AssertionError(f"{run} never reached its gate")


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
    """Every tab, every made file and every answer button is a 44 px tap."""
    small = page.evaluate(
        """[...document.querySelectorAll('[role=tab], .made-file, .action-bar button')]
          .filter(e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed')
          .filter(e => {
            const r = e.getBoundingClientRect();
            return r.width && Math.min(r.width, r.height) < 43.5;
          })
          .map(e => e.outerHTML.slice(0, 120))"""
    )
    assert small == []


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        width = page.viewport_size["width"]  # type: ignore[index]
        page.screenshot(path=f"{SHOTS}/phone-{width}-{name}.png")


def test_a_pick_gate_card_draws_its_items_and_keeps_its_answer_pinned(
    phone: Page, ids: dict[str, str], pick: str
) -> None:
    phone.goto(f"/worlds/{ids['world']}/runs/{pick}")
    card = phone.get_by_test_id("stage-panel")
    expect(card.get_by_test_id("stage-meta")).to_have_text(re.compile(r"^Gate\s*step 2 of 3"))
    expect(card.get_by_role("tablist")).to_have_text(re.compile(r"^Decision\s*All \d+\s*Ask$"))
    expect(card.get_by_test_id("pick-card").first).to_be_visible()
    bar = phone.locator(".action-bar")
    expect(bar).to_be_visible()
    _fits(phone)
    _taps(phone)
    _shot(phone, "gate")

    # Another tab: the answer stays pinned above the tab bar.
    card.get_by_role("tab", name=re.compile(r"^All")).click()
    expect(card.get_by_test_id("tab-all").get_by_test_id("item").first).to_be_visible()
    expect(bar).to_be_visible()
    expect(bar.get_by_role("button", name=re.compile(r"^Continue"))).to_be_in_viewport()
    _fits(phone)
    card.get_by_role("tablist").scroll_into_view_if_needed()
    _shot(phone, "gate-all")

    # Back on the decision, a pick answers the gate.
    card.get_by_role("tab", name="Decision").click()
    card.get_by_test_id("pick-card").first.locator("b").click()
    expect(card.get_by_test_id("pick-card").first.locator("input")).to_be_checked()
    bar.get_by_role("button", name=re.compile(r"^Continue with")).click()
    expect(card.get_by_test_id("answered")).to_be_visible()


def test_done_the_side_column_lists_what_was_made_and_spend_adds_up(
    phone: Page, ids: dict[str, str], base_url: str
) -> None:
    run = httpx.get(f"{base_url}/api/runs/{ids['done']}").json()
    made = [
        a
        for a in httpx.get(f"{base_url}/api/runs/{ids['done']}/artifacts").json()
        if a["type"] not in ("verdict", "decision", "note")
    ]
    phone.goto(f"/worlds/{ids['world']}/runs/{ids['done']}")
    card = phone.get_by_test_id("stage-panel")
    expect(card.get_by_role("tablist")).to_have_text(re.compile(r"^Result\s*Inputs\s*1\s*Details$"))
    side = phone.get_by_test_id("made")
    expect(side.get_by_test_id("made-file")).to_have_count(len(made))
    short = side.get_by_role("link", name=re.compile(r"^short\.mp4.*made by render"))
    expect(short).to_be_visible()
    # Stacked under the card on a phone.
    box, under = card.bounding_box(), side.bounding_box()
    assert box and under and under["y"] >= box["y"] + box["height"]
    rows = phone.get_by_test_id("spend-row")
    expect(rows.first).to_be_visible()
    total = sum(
        float(m.group(1)) for t in rows.all_inner_texts() if (m := re.search(r"\$(\d+\.\d+)", t))
    )
    assert abs(total - run["spent_usd"]) < 0.01 * max(1, len(rows.all_inner_texts()))
    _fits(phone)
    _taps(phone)
    side.scroll_into_view_if_needed()
    _shot(phone, "done")


def test_on_a_desktop_the_side_column_sits_right_of_the_card(
    browser: Browser, base_url: str, ids: dict[str, str]
) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, base_url=base_url)
    page = context.new_page()
    page.goto(f"/worlds/{ids['world']}/runs/{ids['done']}")
    card = page.get_by_test_id("stage-panel").bounding_box()
    side = page.locator(".run-side").bounding_box()
    assert card and side and side["x"] > card["x"] + card["width"] - 1
    expect(page.get_by_role("button", name="Previous step")).to_be_enabled()
    page.get_by_role("button", name="Previous step").click()
    expect(page.get_by_test_id("stage-panel").get_by_role("tablist")).to_have_text(
        re.compile(r"^Activity\s*Items")
    )
    _fits(page)
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/desktop-done.png", full_page=True)
    context.close()
