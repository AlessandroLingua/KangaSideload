"""Persistenza: sessione, storico installazioni, file privati."""
import json
import os

import pytest

from kanga.core import store
from kanga.core.gsa import AppleSession


def test_session_roundtrip(appdata):
    s = AppleSession(cookies={"myacinfo": "x"}, dsid="1", adsid="ADS", tokens={"t": "v"},
                     xcode_token="XT", sk=b"\x01\x02")
    store.save_session(store.session_from_gsa(s, apple_id="mario@icloud.com"))
    data = store.load_session()
    assert data["apple_id"] == "mario@icloud.com"
    assert "password" not in json.dumps(data).lower()
    back = store.session_to_apple(data)
    assert (back.adsid, back.xcode_token, back.sk) == ("ADS", "XT", b"\x01\x02")
    store.clear_session()
    assert store.load_session() is None


def test_corrupted_session_is_ignored(appdata):
    store.base_dir().mkdir(parents=True)
    store.session_path().write_text("{not json")
    assert store.load_session() is None
    store.session_path().write_text("[1, 2]")
    assert store.load_session() is None


@pytest.mark.skipif(os.name == "nt", reason="permessi POSIX")
def test_private_files_are_not_world_readable(appdata):
    store.save_session({"a": 1})
    assert store.session_path().stat().st_mode & 0o077 == 0
    p = store.keys_dir() / "private.pem"
    store.write_private(p, b"key")
    assert p.stat().st_mode & 0o077 == 0


def test_history_dedupe_order_and_remove(appdata):
    assert store.load_history() == []
    store.add_history_entry({"bundle_id": "a", "udid": "U1", "n": 1})
    store.add_history_entry({"bundle_id": "b", "udid": "U1"})
    store.add_history_entry({"bundle_id": "a", "udid": "U2"})
    entries = store.add_history_entry({"bundle_id": "a", "udid": "U1", "n": 2})
    assert [(e["bundle_id"], e["udid"]) for e in entries] == [("a", "U1"), ("a", "U2"), ("b", "U1")]
    assert entries[0]["n"] == 2  # la reinstallazione aggiorna la voce esistente
    assert [(e["bundle_id"], e["udid"]) for e in store.remove_history_entry("a", "U2")] == [
        ("a", "U1"), ("b", "U1")]


def test_history_limit_and_corruption(appdata):
    for i in range(store.MAX_HISTORY + 5):
        store.add_history_entry({"bundle_id": f"b{i}", "udid": "U"})
    assert len(store.load_history()) == store.MAX_HISTORY
    store.history_path().write_text("garbage")
    assert store.load_history() == []
    store.history_path().write_text(json.dumps([{"x": 1}, "no", {"bundle_id": "ok"}]))
    assert store.load_history() == [{"bundle_id": "ok"}]


# ------------------------------------------------------------- credenziali
windows_only = pytest.mark.skipif(os.name != "nt", reason="DPAPI solo su Windows")


@windows_only
def test_credentials_roundtrip_encrypted(appdata):
    assert store.load_credentials() is None and not store.has_credentials()
    store.save_credentials("mario@icloud.com", "segreta-123")
    raw = store.credentials_path().read_text(encoding="utf-8")
    assert "segreta-123" not in raw  # mai in chiaro su disco
    assert store.has_credentials() and store.saved_apple_id() == "mario@icloud.com"
    assert store.load_credentials() == ("mario@icloud.com", "segreta-123")
    store.clear_credentials()
    assert store.load_credentials() is None and not store.has_credentials()


@windows_only
def test_corrupted_credentials_are_ignored(appdata):
    store.base_dir().mkdir(parents=True)
    store.credentials_path().write_text('{"apple_id": "a@b.c", "password_protected": "AAAA"}')
    assert store.load_credentials() is None
    assert store.saved_apple_id() == "a@b.c"
