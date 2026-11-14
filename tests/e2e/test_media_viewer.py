"""The review gate on a phone, as on the sofa: the stage's video plays first, then its contact
sheet at full card width. A tap opens the sheet full screen; a pinch and a double tap zoom the
picture while the page itself stays at scale 1; a drag down closes it. Real touches, through the
DevTools protocol."""

import io
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import httpx
import pytest
from PIL import Image
from playwright.sync_api import Browser, CDPSession, Page, expect

FLOW = """name: Render then review
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
  - gate: review
    ask: Check the motion.
    kind: approve
"""


@pytest.fixture
def app(
    tmp_path: Path, serve: Callable[..., AbstractContextManager[str]]
) -> Iterator[tuple[str, Path]]:
    """An app of its own: its run takes the scripted swarm's sessions and leaves a gate open."""
    with serve(tmp_path) as url:
        yield url, tmp_path


@pytest.fixture
def phone(browser: Browser, app: tuple[str, Path]) -> Iterator[Page]:
    base_url = app[0]
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        base_url=base_url,
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
    )
    yield context.new_page()
    context.close()


def _sheet() -> bytes:
    """A tall contact sheet: 4 columns of 9:16 frames, 6 rows."""
    img = Image.new("RGB", (1080, 2880), "teal")
    for i in range(24):
        x, y = i % 4 * 270, i // 4 * 480
        img.paste((40 + i * 8, 90, 160), (x + 4, y + 4, x + 266, y + 476))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _touch(cdp: CDPSession, kind: str, points: list[tuple[float, float]]) -> None:
    cdp.send(
        "Input.dispatchTouchEvent",
        {
            "type": kind,
            "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(points)],
        },
    )


def _scale(page: Page) -> float:
    return float(page.get_by_test_id("viewer-stage").get_attribute("data-scale") or "0")


def _page_scale(page: Page) -> float:
    return page.evaluate("visualViewport.scale")


def test_tap_the_contact_sheet_pinch_and_double_tap_zoom_it_swipe_down_closes(
    phone: Page, app: tuple[str, Path]
) -> None:
    base_url, data_dir = app
    api = httpx.Client(base_url=f"{base_url}/api", timeout=30)
    world = api.post("/worlds", json={"name": "Sheets"}).json()["id"]
    wf = api.post(f"/worlds/{world}/workflows", json={"yaml": FLOW}).json()["workflow_id"]
    run = api.post(f"/workflows/{wf}/runs", json={}).json()["id"]
    for _ in range(300):
        if api.get(f"/runs/{run}").json()["status"] == "waiting_gate":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the run never reached its review gate")
    # The render stage leaves a contact sheet beside its video, with a sidecar as the video has.
    video = next(a for a in api.get(f"/runs/{run}/artifacts").json() if a["name"] == "short.mp4")
    sidecar = data_dir / "worlds" / world / video["path"]
    assert sidecar.name == "short.mp4.md", video["path"]
    (sidecar.parent / "contact_sheet.png").write_bytes(_sheet())
    (sidecar.parent / "contact_sheet.png.md").write_text(
        sidecar.read_text().replace("short.mp4", "contact_sheet.png").replace("video", "image")
    )

    phone.goto("/gates")
    card = phone.get_by_test_id(f"gate-{run}")
    expect(card).to_contain_text("Check the motion.", timeout=20_000)
    shown = card.get_by_test_id("under-review").locator("figure")
    expect(shown).to_have_count(2)
    # The video first, then the sheet.
    expect(shown.first.get_by_test_id("player")).to_be_visible()
    sheet = card.get_by_role("button", name="Expand contact_sheet")
    expect(sheet).to_be_visible()
    expect(sheet).to_contain_text("Tap to expand")
    box = sheet.bounding_box()
    assert box and box["width"] > 300, box  # full card width, not a thumbnail

    sheet.tap()
    viewer = phone.get_by_role("dialog", name="contact_sheet")
    expect(viewer).to_be_visible()
    assert _scale(phone) == 1

    # Pinch out with two fingers about the middle of the screen.
    cdp = phone.context.new_cdp_session(phone)
    cx, cy = 195, 422
    _touch(cdp, "touchStart", [(cx - 40, cy), (cx + 40, cy)])
    for i in range(1, 11):
        _touch(cdp, "touchMove", [(cx - 40 - 8 * i, cy), (cx + 40 + 8 * i, cy)])
        phone.wait_for_timeout(16)
    _touch(cdp, "touchEnd", [])
    assert _scale(phone) > 2, _scale(phone)
    assert _page_scale(phone) == 1

    # A double tap goes back to the whole picture, and another zooms to 2x.
    phone.touchscreen.tap(cx, cy)
    phone.touchscreen.tap(cx, cy)
    expect(phone.get_by_test_id("viewer-stage")).to_have_attribute("data-scale", "1.00")
    phone.wait_for_timeout(400)
    phone.touchscreen.tap(cx, cy)
    phone.touchscreen.tap(cx, cy)
    expect(phone.get_by_test_id("viewer-stage")).to_have_attribute("data-scale", "2.00")
    assert _page_scale(phone) == 1
    phone.wait_for_timeout(400)
    phone.touchscreen.tap(cx, cy)
    phone.touchscreen.tap(cx, cy)
    expect(phone.get_by_test_id("viewer-stage")).to_have_attribute("data-scale", "1.00")

    # Whole again, a drag down closes it, and the gate is where it was.
    _touch(cdp, "touchStart", [(cx, 300)])
    for i in range(1, 13):
        _touch(cdp, "touchMove", [(cx, 300 + 30 * i)])
        phone.wait_for_timeout(16)
    _touch(cdp, "touchEnd", [])
    expect(viewer).to_be_hidden()
    expect(card).to_be_visible()
    assert _page_scale(phone) == 1

    # Android's back closes it too, without leaving the page.
    url = phone.url
    sheet.tap()
    expect(viewer).to_be_visible()
    phone.go_back()
    expect(viewer).to_be_hidden()
    assert phone.url == url
