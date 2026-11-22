"""World Logins and the Secrets Store: the file backend's contract (which a later 1Password
adapter must pass too), what a login refuses, and that no read of a login carries a secret."""

import json
import shutil
import stat
import sys
from contextlib import closing
from pathlib import Path

import pytest

from swarmify_connectors import Connectors
from swarmify_connectors.logins import FileSecrets, SecretStore, Taken, domain_problem, totp
from swarmify_connectors.packages import MANIFEST
from swarmify_kernel.sqlite import connect

SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture
def store(tmp_path: Path) -> SecretStore:
    return FileSecrets(tmp_path / "data" / "secrets.json")


def test_the_secrets_store_contract(store: SecretStore, tmp_path: Path) -> None:
    assert store.get("logins/w/a/password") is None
    store.put("logins/w/a/password", "hunter2")
    store.put("logins/w/a/session", '{"cookies": []}')
    store.put("logins/w/ab/password", "other")
    assert store.get("logins/w/a/password") == "hunter2"
    store.delete("logins/w/a/session")
    store.delete("logins/w/a/session")  # deleting nothing is fine
    assert store.get("logins/w/a/session") is None
    store.delete_prefix("logins/w/a/")
    assert store.get("logins/w/a/password") is None
    assert store.get("logins/w/ab/password") == "other"  # a prefix is a folder, not a stem


def test_the_secrets_file_is_mode_600_and_refused_when_others_can_read_it(
    store: SecretStore, tmp_path: Path
) -> None:
    store.put("logins/w/a/password", "hunter2")
    file = tmp_path / "data" / "secrets.json"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    file.chmod(0o644)
    with pytest.raises(PermissionError, match="chmod 600"):
        store.get("logins/w/a/password")


@pytest.mark.parametrize(
    ("domain", "ok"),
    [
        ("admin.myshop.com", True),
        ("127.0.0.1:8123", True),
        ("localhost", True),
        ("*.myshop.com", True),
        ("*.com", False),
        ("*", False),
        ("https://myshop.com", False),
        ("my shop.com", False),
        ("", False),
    ],
)
def test_domains_follow_the_sandboxs_rules(domain: str, ok: bool) -> None:
    assert (domain_problem(domain) is None) is ok


def test_totp_matches_rfc_6238() -> None:
    # RFC 6238's SHA-1 vector: the ASCII key "12345678901234567890", T = 59 s, 8 digits 94287082
    key = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp(key, 59) == "287082"


def test_a_login_is_added_listed_changed_rotated_and_deleted_without_its_secrets(
    connectors: Connectors, tmp_path: Path, db: Path
) -> None:
    logins = connectors.logins
    added = logins.add(
        "w1", "shop_admin", ["Admin.MyShop.com ", "*.cdn.myshop.com"], "ana", "pa55-word",
        "jbsw y3dp ehpk 3pxp",
    )  # fmt: skip
    assert added.domains == ["admin.myshop.com", "*.cdn.myshop.com"]
    assert (added.has_totp, added.has_session) == (True, False)
    with pytest.raises(Taken):
        logins.add("w1", "shop_admin", ["x.com"], "b", "c")
    for bad, field in [
        (("Shop", ["x.com"], "u", "p"), "Name"),
        (("s", ["*.com"], "u", "p"), "Domains: \\*.com is too broad"),
        (("s", [], "u", "p"), "Domains"),
        (("s", ["x.com"], "u", "two\nlines"), "Password"),
        (("s", ["x.com"], "u", "p", "not base32!"), "Authenticator secret"),
    ]:
        with pytest.raises(ValueError, match=field):
            logins.add("w1", *bad)  # type: ignore[arg-type]
    assert logins.get("w1", "s") is None  # a refused add keeps nothing

    connectors.logins.keep_session("w1", "shop_admin", '{"cookies": [{"value": "c00kie-value"}]}')
    changed = logins.change("w1", "shop_admin", username="ana2")
    assert changed.username == "ana2" and changed.has_session
    rotated = logins.rotate("w1", "shop_admin", "n3w-pass", clear_totp=True)
    assert (rotated.has_totp, rotated.has_session) == (False, False)  # rotating signs it out
    handed = logins.hand("w1", "shop_admin")
    assert (handed.password, handed.totp, handed.session) == ("n3w-pass", None, None)
    logins.used("w1", "run_1", "stg_1", "browser", "shop_admin", "signed_in")
    assert [u.result for u in logins.uses("w1", "shop_admin")] == ["signed_in"]
    assert logins.get("w1", "shop_admin").last_used_at  # type: ignore[union-attr]

    secrets = ("pa55-word", "n3w-pass", SECRET, "c00kie-value")
    events = connectors.storage.login_uses("w1", None, 100)
    with closing(connect(db)) as conn:  # every event of the world, as the stream serves it
        payloads = [r[0] for r in conn.execute("SELECT payload FROM events")]
    for value in secrets:
        assert value not in json.dumps([str(logins.list_all("w1")), payloads, str(events)])

    assert logins.delete("w1", "shop_admin")
    assert not logins.delete("w1", "shop_admin")
    file = json.loads((tmp_path / "secrets.json").read_text())
    assert not [k for k in file if k.startswith("logins/w1/shop_admin/")]


def test_a_deleted_world_takes_its_logins_and_their_secrets(
    connectors: Connectors, tmp_path: Path
) -> None:
    connectors.logins.add("w1", "a", ["a.com"], "u", "p1")
    connectors.logins.add("w2", "a", ["a.com"], "u", "p2")
    connectors.delete_world("w1")
    assert connectors.logins.list_all("w1") == []
    assert connectors.logins.hand("w2", "a").password == "p2"
    assert "p1" not in (tmp_path / "secrets.json").read_text()


SIGNER = Path(__file__).parent / "fixtures/packages/signer"


def test_a_swarm_stage_hands_its_login_only_to_tools_that_accept_one(
    connectors: Connectors, tmp_path: Path
) -> None:
    """Only the signer's server gets the login, confined to the stage, its session in a mode-600
    file kept out of the sandbox plan; `settle` keeps the session it saved and deletes both."""
    for name, source in (("signer", SIGNER), ("probe", SIGNER.parent / "probe")):
        copy = tmp_path / "src" / name
        shutil.copytree(source, copy)
        toml = (copy / MANIFEST).read_text()
        (copy / MANIFEST).write_text(
            toml.replace('["python3", "server.py"]', f'["{sys.executable}", "server.py"]')
        )
        connectors.packages.install(str(copy))
    connectors.setups.put("probe", "w1", {"api_key": "k"}, {})
    for name in ("signer", "probe"):
        connectors.enable("w1", name, "1.0.0")
    connectors.logins.add("w1", "shop", ["shop.example.com"], "ana", "pa55-word", SECRET)
    connectors.logins.keep_session("w1", "shop", '{"sid": "s3ss-old-777"}')

    servers = connectors.agent_servers(
        "w1", ["signer", "probe"], run=("run_1", "stg_1"), login="shop"
    )
    signer, probe = servers["signer"], servers["probe"]
    assert signer["env"]["SWARMIFY_LOGIN_PASSWORD"] == "pa55-word"
    assert signer["env"]["SWARMIFY_LOGIN_TOTP"] == SECRET
    assert not [k for k in probe["env"] if k.startswith("SWARMIFY_LOGIN")]
    home = Path(signer["env"]["SWARMIFY_LOGIN_SESSION"]).parent
    plan = (home.parent / "plan.json").read_text()
    assert "pa55-word" not in plan and SECRET not in plan and "s3ss-old-777" not in plan
    assert json.loads(plan)["sandbox"]["network"]["allowedDomains"] == ["shop.example.com"]
    assert "/runs/run_1/stg_1" in plan  # it writes the stage's folder, confined to it
    session = home / "login-session.json"
    assert stat.S_IMODE(session.stat().st_mode) == 0o600
    (home / "login-session.out.json").write_text('{"sid": "new"}')

    connectors.settle("run_1", "stg_1")
    assert connectors.logins.hand("w1", "shop").session == '{"sid": "new"}'
    assert not session.exists() and not (home / "login-session.out.json").exists()
    [use] = connectors.logins.uses("w1", "shop")
    assert (use.run_id, use.stage_id, use.package, use.result) == (
        "run_1", "stg_1", "signer", "received"
    )  # fmt: skip
    assert "SWARMIFY_LOGIN_PASSWORD" in connectors.secret_names("w1")
