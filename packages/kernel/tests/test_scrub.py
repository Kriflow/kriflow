"""Scrubbing a stage's secrets: by stage, by run, or every held one; longest first."""

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
