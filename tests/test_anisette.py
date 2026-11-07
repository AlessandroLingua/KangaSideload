"""Scelta della fonte anisette (iTunes o emulata) e header coerenti per GSA."""
import pytest

from kanga.core import anisette_provider as ap
from kanga.core.gsa import CLIENT_INFO, GsaClient


class Source:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.calls = 0

    def get_headers(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} non disponibile")
        return {"X-Apple-I-MD": self.name, "X-Mme-Client-Info": f"<{self.name}>"}


def test_prefers_itunes_when_it_works():
    itunes, emu = Source("itunes"), Source("emu")
    a = ap.AutoAnisette("auto", itunes_factory=lambda: itunes, emulated_factory=lambda: emu)
    assert a.get_headers()["X-Apple-I-MD"] == "itunes"
    assert a.get_headers()["X-Apple-I-MD"] == "itunes"
    assert a.source == ap.SOURCE_ITUNES and emu.calls == 0


def test_falls_back_to_emulated_and_sticks_to_it():
    itunes, emu = Source("itunes", fail=True), Source("emu")
    a = ap.AutoAnisette("auto", itunes_factory=lambda: itunes, emulated_factory=lambda: emu)
    assert a.get_headers()["X-Apple-I-MD"] == "emu"
    itunes.fail = False  # la scelta non cambia a metà: login e firma con la stessa identità
    assert a.get_headers()["X-Apple-I-MD"] == "emu"
    assert a.source == ap.SOURCE_EMULATED and "non disponibile" in a.fallback_reason


def test_itunes_factory_error_falls_back():
    def broken():
        raise OSError("DLL Apple non trovate")

    emu = Source("emu")
    a = ap.AutoAnisette("auto", itunes_factory=broken, emulated_factory=lambda: emu)
    assert a.get_headers()["X-Apple-I-MD"] == "emu"


def test_forced_modes():
    itunes, emu = Source("itunes"), Source("emu")
    a = ap.AutoAnisette("emulata", itunes_factory=lambda: itunes, emulated_factory=lambda: emu)
    assert a.get_headers()["X-Apple-I-MD"] == "emu" and itunes.calls == 0
    b = ap.AutoAnisette("itunes", itunes_factory=lambda: Source("x", fail=True), emulated_factory=lambda: emu)
    with pytest.raises(ap.AnisetteError, match="iTunes"):
        b.get_headers()


def test_mode_from_environment(monkeypatch):
    monkeypatch.setenv("KANGA_ANISETTE", "emulata")
    assert ap.AutoAnisette().mode == "emulata"


def test_gsa_client_info_follows_anisette():
    assert GsaClient.client_info({"X-Mme-Client-Info": "<win>"}) == "<win>"
    assert GsaClient.client_info({"X-MMe-Client-Info": "<mac>"}) == "<mac>"
    assert GsaClient.client_info({}) == CLIENT_INFO
    client = GsaClient(lambda: {"X-Mme-Client-Info": "<win>", "X-Apple-I-MD": "otp"})
    headers = client._anisette_http_headers()
    keys = [k for k in headers if k.lower() == "x-mme-client-info"]
    assert keys == ["X-MMe-Client-Info"] and headers["X-MMe-Client-Info"] == "<win>"
    assert headers["X-Apple-I-MD"] == "otp"


def test_retry_itunes_after_installing_it():
    itunes, emu = Source("itunes", fail=True), Source("emu")
    a = ap.AutoAnisette("auto", itunes_factory=lambda: itunes, emulated_factory=lambda: emu)
    assert a.get_headers()["X-Apple-I-MD"] == "emu"
    itunes.fail = False          # l'utente installa iTunes e iCloud
    a.retry_itunes()             # nuovo login
    assert a.get_headers()["X-Apple-I-MD"] == "itunes"
    assert a.source == ap.SOURCE_ITUNES and a.fallback_reason == ""
    assert itunes.calls == 2     # la prova riusa i propri header: nessuna chiamata doppia
