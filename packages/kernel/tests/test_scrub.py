"""Scrubbing a stage's secrets: by stage, by run, or every held one; longest first."""

import base64
import json
from urllib.parse import quote, quote_plus

from swarmify_kernel import scrub


def test_values_are_replaced_by_their_login_until_the_stage_drops_them() -> None:
    scrub.add("stg_1", "run_1", "shop", ["hunter2", "hunter2-long", None, ""])
    scrub.add("stg_2", "run_2", "bank", ["b4nk-pass"])
    try:
        assert scrub.text("x hunter2-long y", "stg_1") == "x [secret: shop] y"
        assert scrub.text("b4nk-pass", "stg_1") == "b4nk-pass"  # another stage's, not this one's
        assert scrub.text("b4nk-pass", run_id="run_2") == "[secret: bank]"
        assert scrub.value({"a": ["hunter2", 3], "b4nk-pass": None}) == {
            "a": ["[secret: shop]", 3], "b4nk-pass": None
        }  # fmt: skip
        assert scrub.cookies('{"cookies": [{"value": "short"}, {"value": "long-enough"}]}') == [
            "long-enough"
        ]
    finally:
        scrub.drop("stg_1")
        scrub.drop("stg_2")
    assert scrub.text("hunter2") == "hunter2" and not scrub.holds("stg_1")


def test_a_value_is_found_json_escaped_url_encoded_and_in_a_basic_header() -> None:
    """Regression (security review of #578): the PoC's password, as a JSON string, a query and a
    Basic header carry it, is scrubbed in each form; a value under 6 characters is never listed
    (it would mangle ordinary text); an event of no run is scrubbed by its world's values only."""
    pw = 'pa"ss\\w0rd/\u00e9+&'
    scrub.add("s", "r", "shop", [pw], username="ana", world_id="w1")
    scrub.add("s2", "r2", "tiny", ["e"], world_id="w2")
    scrub.add("s3", "r3", "bank", ["b4nk-pass"], world_id="w3")
    try:
        assert scrub.text(json.dumps({"pw": pw}), "s") == '{"pw": "[secret: shop]"}'
        assert (
            scrub.text(json.dumps({"pw": pw}, ensure_ascii=False), "s")
            == '{"pw": "[secret: shop]"}'
        )
        assert scrub.text("POST /login?p=" + quote(pw, safe=""), "s").endswith("[secret: shop]")
        assert scrub.text("p=" + quote_plus(pw), "s") == "p=[secret: shop]"
        basic = base64.b64encode(f"ana:{pw}".encode()).decode()
        assert (
            scrub.text(f"Authorization: Basic {basic}", "s")
            == "Authorization: Basic [secret: shop]"
        )
        assert scrub.value({"msg": "release complete"}, run_id="r2") == {"msg": "release complete"}
        assert scrub.text("b4nk-pass", world_id="w1") == "b4nk-pass"  # another world's
        assert scrub.text("b4nk-pass", world_id="w3") == "[secret: bank]"
    finally:
        for stage in ("s", "s2", "s3"):
            scrub.drop(stage)
