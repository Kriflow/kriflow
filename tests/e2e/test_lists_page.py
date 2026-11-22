"""The Lists page at phone size (issue 540): an operator on the sofa finds pitched Entries waiting
through Needs you, approves two and rejects one in batches with a reason, restores the rejected
one, edits a score, and opens an Entry's file, run and history. Nothing is wider than the
screen and every tap is a 44 px thumb, at 390 and 360 wide."""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

SIZES: list[ViewportSize] = [{"width": 390, "height": 844}, {"width": 360, "height": 740}]
LIST = {
    "statuses": ["pitched", "new", "making", "parked", "rejected"],
    "rejected": ["rejected"],
    "review": ["pitched"],
}
GATE = "name: Make it\nstages:\n  - gate: brief\n    ask: Go?\n"
FITS = """() => {
  const out = [];
  if (document.documentElement.scrollWidth > innerWidth) out.push('page scrolls sideways');
  const taps = 'main .tab, main button:not(.link), main a.button, main label.check';
  for (const e of document.querySelectorAll(taps))
    if (e.checkVisibility() && e.getBoundingClientRect().height < 43.5)
      out.push(`${e.className} "${(e.textContent || '').trim().slice(0, 20)}" is short`);
  return out;
}"""


@pytest.fixture
def world(base_url: str, data_dir: Path) -> Iterator[dict[str, str]]:
    """A world whose ideas list has three pitched Entries; `a` has a file and a run that held it."""
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    world = api.post("/worlds", json={"name": "Lists"}).json()["id"]
    api.put(f"/worlds/{world}/lists/ideas", json=LIST).raise_for_status()
    url = f"/worlds/{world}/lists/ideas"
    api.put(f"/worlds/{world}/assets/pitch.md", content=b"# Pitch A\n\nWhy it scores 7.")
    for key, score in (("a", 7), ("b", 5), ("c", 3)):
        entry = {"key": key, "title": f"Idea {key.upper()}", "score": score}
        entry |= {"fields": {"kind": "one"}, "file": "assets/pitch.md" if key == "a" else None}
        api.post(f"{url}/entries", json=entry).raise_for_status()
    workflow = api.post(f"/worlds/{world}/workflows", json={"yaml": GATE}).json()["workflow_id"]
    run = api.post(f"/workflows/{workflow}/runs", json={}).json()["id"]
    for _ in range(200):
        if api.get(f"/runs/{run}").json()["status"] == "waiting_gate":
            break
        time.sleep(0.1)
    # ponytail: a claim's record written straight to the store until fan-out claims (issue 541).
    with closing(sqlite3.connect(data_dir / "swarmify.db")) as db, db:
        db.execute(
            "UPDATE list_entries SET runs = json_array(?) WHERE world_id = ? AND key = 'a'",
            (run, world),
        )
    yield {"world": world, "run": run, "api": f"{base_url}/api{url}"}
    api.post(f"/runs/{run}/abort")  # its open gate would sit in later suites' inbox


@pytest.fixture(params=SIZES, ids=lambda s: f"{s['width']}")
def phone(request: pytest.FixtureRequest, browser: Browser, base_url: str) -> Iterator[Page]:
    context = browser.new_context(
        viewport=request.param, base_url=base_url, is_mobile=True, has_touch=True
    )
    yield context.new_page()
    context.close()


def fits(page: Page) -> None:
    page.wait_for_timeout(400)  # a sheet has slid in or out
    assert page.evaluate(FITS) == []


def move(page: Page, reason: str, to: str) -> None:
    sheet = page.locator("dialog.move-sheet")
    expect(sheet).to_be_visible()
    fits(page)
    sheet.get_by_label("Reason (optional)").fill(reason)
    sheet.get_by_role("button", name=to, exact=False).first.click()
    expect(sheet).to_be_hidden()


def test_curating_a_list_from_a_phone(phone: Page, world: dict[str, str]) -> None:
    w = world["world"]
    entry = lambda key: httpx.get(f"{world['api']}/entries/{key}").json()  # noqa: E731

    # Needs you says the list waits and leads to it.
    phone.goto("/gates")
    group = phone.get_by_test_id("notices-review")
    expect(group).to_contain_text("1 list waits for you")
    group.locator("summary").click()
    expect(group).to_contain_text("Lists: 3 Entries wait for you in ideas (pitched).")
    group.get_by_role("link", name="Open Lists").click()

    # It opens on what waits: the three pitched cards, best score first.
    expect(phone.get_by_role("heading", name="Lists", level=1)).to_be_visible()
    cards = phone.get_by_test_id("entry")
    expect(cards).to_have_count(3)
    expect(cards.first).to_contain_text("Idea A")
    fits(phone)

    # Batch: tick two, move them to new with one reason.
    for title in ("Idea A", "Idea B"):
        phone.get_by_label(f"Select {title}").check()
    batch = phone.get_by_test_id("batch")
    expect(batch).to_contain_text("2 selected")
    fits(phone)
    batch.get_by_role("button", name="Move 2").click()
    move(phone, "Good week", "new")
    expect(cards).to_have_count(1)
    assert entry("a")["status"] == entry("b")["status"] == "new"

    # The last one: rejected with a reason, from its own sheet. Nothing waits any more.
    cards.first.get_by_role("button").click()
    sheet = phone.get_by_test_id("entry-sheet")
    expect(sheet).to_contain_text("pitched")
    fits(phone)
    sheet.get_by_role("button", name="Move").click()
    move(phone, "Not for us", "rejected")
    expect(sheet).to_contain_text("pitched to rejected by operator: Not for us")
    phone.keyboard.press("Escape")
    expect(phone.get_by_test_id("no-entries")).to_be_visible()
    notices = httpx.get(f"{world['api'].split('/api')[0]}/api/notices").json()
    assert not [n for n in notices if n["kind"] == "review" and n["world_id"] == w]

    # Restore it from the rejected chip.
    phone.get_by_test_id("statuses").get_by_role("link", name="rejected").click()
    expect(cards).to_have_count(1)
    cards.first.get_by_role("button").click()
    sheet.get_by_role("button", name="Move").click()
    move(phone, "Second look", "new")
    phone.keyboard.press("Escape")
    assert entry("c")["status"] == "new"

    # Edit A's score; its history says who moved it and why; its file and its run open.
    phone.get_by_test_id("statuses").get_by_role("link", name="new").click()
    expect(cards).to_have_count(3)
    cards.filter(has_text="Idea A").get_by_role("button").click()
    expect(sheet).to_contain_text("pitched to new by operator: Good week")
    sheet.get_by_label("Score").fill("9.5")
    sheet.get_by_role("button", name="Save").click()
    expect(phone.get_by_text("Saved.")).to_be_visible()
    assert entry("a")["score"] == 9.5
    expect(sheet.get_by_role("link", name="Make it")).to_be_visible()
    fits(phone)
    sheet.get_by_role("link", name="Open file").click()
    expect(phone.locator("main")).to_contain_text("Why it scores 7.")
    phone.go_back()
    cards.filter(has_text="Idea A").get_by_role("button").click()
    sheet.get_by_role("link", name="Make it").click()
    expect(phone).to_have_url(f"{phone.url.split('/worlds')[0]}/worlds/{w}/runs/{world['run']}")
