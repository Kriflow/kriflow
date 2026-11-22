"""The Logins row on Settings at phone size (spec: World Logins, seam 5), against the real API:
add a login, see it listed without its password (in the page and in every response), change its
password, and find its delete refused while a saved workflow names it. At 390 and 360 wide."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Response, ViewportSize, expect
from test_phone import PASSWORD, signed_in

SIGNER = Path(__file__).parents[2] / "packages/connectors/tests/fixtures/packages/signer"
SECRET, NEW_SECRET = "Pa55-word-e2e-31", "N3w-pa55-e2e-77"


@pytest.fixture(params=[390, 360])
def page(browser: Browser, locked_url: str, request: pytest.FixtureRequest) -> Iterator[Page]:
    size: ViewportSize = {"width": request.param, "height": 800}
    context = browser.new_context(
        viewport=size, base_url=locked_url, is_mobile=True, has_touch=True
    )
    context.request.post("/api/session", data={"password": PASSWORD})
    yield context.new_page()
    context.close()


def fits(page: Page) -> None:
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


def test_a_login_is_added_rotated_and_its_delete_refused_on_a_phone(
    page: Page, locked_url: str
) -> None:
    api = signed_in(locked_url)
    world = api.post("/worlds", json={"name": "Shop"}).json()["id"]
    review = api.post("/packages/review", json={"source": str(SIGNER)})
    if review.status_code == 200:  # installed once for the whole install
        approved = review.json()["permissions"]
        api.post("/packages", json={"source": str(SIGNER), "approved": approved})
    api.put(f"/worlds/{world}/packages/signer", json={"version": "1.0.0"}).raise_for_status()
    bodies: list[str] = []

    def keep(response: Response) -> None:
        if "/api/" in response.url and "/events" not in response.url:
            try:
                bodies.append(response.text())
            except Exception:  # a body gone with the page, or a stream
                pass

    page.on("response", keep)
    page.goto(f"/worlds/{world}/settings")
    row = page.get_by_role("region", name="Logins")
    expect(row).to_contain_text("Let a workflow sign in to a site for you")
    fits(page)
    row.get_by_role("button", name="Add a login").click()
    sheet = page.get_by_role("dialog", name="Add a login")
    sheet.get_by_label("Domains", exact=True).fill("admin.myshop.com\n*.myshop.com")
    expect(sheet.get_by_label("Name", exact=True)).to_have_value("myshop_admin")
    sheet.get_by_label("Name", exact=True).fill("shop_admin")
    sheet.get_by_label("Username", exact=True).fill("ana")
    sheet.get_by_label("Password", exact=True).fill(SECRET)
    fits(page)
    sheet.get_by_role("button", name="Save login").click()
    card = row.get_by_role("button", name="shop_admin, ana")
    expect(card).to_contain_text("Never used")
    assert card.bounding_box()["height"] >= 44  # type: ignore[index]

    yaml = (
        "name: Pilot\nstages:\n  - connector: order\n    calls: signer\n"
        "    params: {login: shop_admin}\n"
    )
    api.post(f"/worlds/{world}/workflows", json={"yaml": yaml}).raise_for_status()
    page.reload()
    page.get_by_role("button", name="shop_admin, ana").click()
    login = page.get_by_role("dialog", name="shop_admin")
    login.get_by_role("button", name="Change password").click()
    login.get_by_label("New password", exact=True).fill(NEW_SECRET)
    login.get_by_role("button", name="Save password").click()
    expect(page.get_by_text("Password changed.")).to_be_visible()
    login.get_by_role("button", name="Delete login").click()
    expect(login.get_by_test_id("delete-refused")).to_have_text(
        "Pilot names this login. Remove it from their stages first."
    )
    expect(login.get_by_role("button", name="Delete", exact=True)).to_have_count(0)
    fits(page)

    html = page.content()
    for secret in (SECRET, NEW_SECRET):
        assert secret not in html
        assert all(secret not in b for b in bodies)
    api.close()
