"""Settings under both names (kriflow#594): the new name wins, the old one still counts."""

from pathlib import Path

from kriflow_kernel.settings import data_dir, old_settings_line, setting


def test_the_new_name_wins_an_empty_one_falls_back_and_the_default_comes_last() -> None:
    both = {"KRIFLOW_BASE_URL": "https://new", "SWARMIFY_BASE_URL": "https://old"}
    assert setting("BASE_URL", "d", both) == "https://new"
    assert setting("BASE_URL", "d", {"KRIFLOW_BASE_URL": "", "SWARMIFY_BASE_URL": "o"}) == "o"
    assert setting("BASE_URL", "d", {}) == "d"


def test_the_startup_line_names_each_old_setting_still_set() -> None:
    env = {"SWARMIFY_PASSWORD": "x", "SWARMIFY_RESOURCES": "", "KRIFLOW_BASE_URL": "u"}
    assert old_settings_line(env) == (
        "Read old settings: SWARMIFY_PASSWORD. Rename them to KRIFLOW_* in .env;"
        " the old names stop working in a later release."
    )
    assert old_settings_line({"KRIFLOW_PASSWORD": "x"}) is None


def test_the_data_dir_is_kriflow_unless_only_an_old_swarmify_one_exists(tmp_path: Path) -> None:
    assert data_dir({}, tmp_path) == tmp_path / ".kriflow"
    (tmp_path / ".swarmify").mkdir()
    assert data_dir({}, tmp_path) == tmp_path / ".swarmify"  # nothing is moved
    (tmp_path / ".kriflow").mkdir()
    assert data_dir({}, tmp_path) == tmp_path / ".kriflow"
    assert data_dir({"SWARMIFY_DATA_DIR": "/d"}, tmp_path) == Path("/d")
