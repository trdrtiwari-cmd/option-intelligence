"""Push storage lives in the accounts database (survives Render redeploys). Run: python tests/test_push_storage.py"""
import importlib, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def _fresh_accounts():
    os.environ.pop("DATABASE_URL", None)
    os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
    sys.path.insert(0, str(ROOT))
    import accounts
    return importlib.reload(accounts)


def test_push_roundtrip_in_accounts_db():
    acc = _fresh_accounts()
    uid = acc.create_user("pushuser", "correct horse battery 42")["id"]
    acc.push_upsert_device(uid, "web", "https://e/1", "{}", "t")
    acc.push_upsert_device(uid, "web", "https://e/1", '{"a":1}', "t")  # upsert, not duplicate
    acc.push_upsert_device(uid, "native", "tok" * 20, "tok" * 20, "t")
    web = acc.push_devices("web", uid)
    assert len(web) == 1 and web[0]["payload"] == '{"a":1}'
    assert len(acc.push_devices("native", 0)) == 1          # owner unset -> all accounts
    assert acc.push_recipient_ids(0) == [uid] and acc.push_recipient_ids(uid) == [uid]
    acc.push_delete_device(web[0]["id"])
    assert acc.push_devices("web", uid) == []
    acc.push_add_history(uid, "CONFIRMED", "NIFTY", "CALL", "t", "b", "now")
    acc.push_add_history(uid, "CONFIRMED", "NIFTY", "PUT", "t2", "b", "now")
    hist = acc.push_history(uid, 10)
    assert [h["side"] for h in hist] == ["PUT", "CALL"]


def test_main_uses_accounts_db_not_local_sqlite():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "push_notifications.sqlite3" not in source and "PUSH_DB" not in source
    assert "acc.push_devices" in source and "acc.push_upsert_device" in source
    assert '@app.post("/api/push/native/register")' in source


def test_vapid_keys_autogenerate_and_persist():
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    os.environ.pop("VAPID_PUBLIC_KEY", None); os.environ.pop("VAPID_PRIVATE_KEY", None)
    acc = _fresh_accounts()
    pub, priv = acc.vapid_keys()
    assert acc.vapid_keys() == (pub, priv)                      # stable across calls (stored in the database)
    d = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    assert len(d(priv)) == 32 and len(d(pub)) == 65 and d(pub)[0] == 4
    key = ec.derive_private_key(int.from_bytes(d(priv), "big"), ec.SECP256R1())
    assert key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint) == d(pub)
    os.environ["VAPID_PUBLIC_KEY"], os.environ["VAPID_PRIVATE_KEY"] = "P", "Q"
    assert acc.vapid_keys() == ("P", "Q")                       # env override wins
    os.environ.pop("VAPID_PUBLIC_KEY"); os.environ.pop("VAPID_PRIVATE_KEY")


if __name__ == "__main__":
    test_push_roundtrip_in_accounts_db()
    test_main_uses_accounts_db_not_local_sqlite()
    test_vapid_keys_autogenerate_and_persist()
    print("all push storage tests passed")
