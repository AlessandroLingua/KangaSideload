"""Flusso completo di sideload con servizi Apple, firma e dispositivo simulati."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fakes import fake_mobileprovision, make_ipa, new_key_pem, self_signed_cert
from kanga.core import apple_services as aps
from kanga.core import appflow, store
from kanga.core.appflow import STEPS, AppFlow, AppFlowError, _unique_bundle_id
from kanga.core.gsa import AppleSession
from kanga.core.signer import ZsignError

EXPIRES = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
UDID = "00008110-001A2B3C4D5E"


class FakeServices:
    """Simula developerservices2.apple.com per un account gratuito."""

    def __init__(self, certs=(), devices=(), app_ids=()):
        self.certs = list(certs)
        self.devices = list(devices)
        self.app_ids = list(app_ids)
        self.revoked: list[str] = []
        self.registered: list[tuple[str, str]] = []
        self.csr_submitted = False
        self.added_app_ids: list[str] = []

    def list_teams(self):
        return [{"teamId": "TEAM123", "name": "Mario Rossi"}]

    def list_devices(self, team_id):
        return [{"deviceNumber": d} for d in self.devices]

    def register_device(self, team_id, udid, name="Kanga device"):
        self.registered.append((udid, name))
        self.devices.append(udid)

    def list_certificates(self, team_id):
        return list(self.certs)

    def revoke_certificate(self, team_id, serial):
        self.revoked.append(serial)
        self.certs = [c for c in self.certs if c.serial_number != serial]

    def submit_csr(self, team_id, csr_pem, machine_name="Kanga"):
        from cryptography import x509

        self.csr_submitted = True
        assert x509.load_pem_x509_csr(csr_pem.encode()).is_signature_valid
        # come Apple: la risposta contiene solo il seriale, il certificato compare nella lista
        return aps.Certificate(serial_number="NEWSERIAL")

    def add_app_id(self, team_id, bundle_id, name="Kanga App"):
        self.added_app_ids.append(bundle_id)
        entry = {"identifier": bundle_id, "appIdId": "APPID1", "name": name}
        self.app_ids.append(entry)
        return entry

    def list_app_ids(self, team_id):
        return list(self.app_ids)

    def download_profile(self, team_id, app_id_id):
        bundle = next(a["identifier"] for a in self.app_ids if a["appIdId"] == app_id_id)
        return aps.ProvisioningProfile(raw=fake_mobileprovision("TEAM123", bundle, EXPIRES))


class Harness:
    def __init__(self, monkeypatch, tmp_path, services: FakeServices, sign_error=None):
        self.services = services
        self.events: list[tuple[str, object]] = []
        self.sign_args: dict = {}
        self.temp_seen: list[Path] = []
        self.installed: list[tuple[str, Path]] = []
        self.ipa = make_ipa(tmp_path / "Camera.ipa", name="Camera", bundle="com.example.camera")

        monkeypatch.setattr(aps, "AppleServicesClient", lambda session, getter: services)

        def fake_sign(**kw):
            self.sign_args = kw
            for k in ("p12_path", "mobileprovision_path", "entitlements_path"):
                assert Path(kw[k]).exists(), f"{k} deve esistere durante la firma"
                self.temp_seen.append(Path(kw[k]))
            if sign_error:
                raise sign_error
            kw["on_output"](">>> Signing: Camera")
            kw["on_output"]("debug noise")
            Path(kw["ipa_out"]).write_bytes(b"signed")
            return kw["ipa_out"]

        def fake_install(udid, path, on_progress=None):
            self.installed.append((udid, Path(path)))
            on_progress(None, "Caricamento sul dispositivo")
            on_progress(40, "")
            on_progress(100, "Completata")

        monkeypatch.setattr(appflow, "sign_ipa", fake_sign)
        monkeypatch.setattr(appflow.device, "install_ipa", fake_install)
        self.flow = AppFlow(on_event=lambda tag, data: self.events.append((tag, data)))
        self.session = AppleSession(adsid="000123-ADSID", dsid="1", xcode_token="XT")

    def steps(self):
        return [d for t, d in self.events if t == "step"]

    def logs(self):
        return [d for t, d in self.events if t == "log"]

    def run(self, **kw):
        return self.flow.sideload(self.session, self.ipa, UDID, **kw)


def _issue_cert_for_saved_key(services: FakeServices, monkeypatch):
    """Dopo submit_csr, la lista certificati contiene il certificato della chiave appena generata."""
    original = aps.generate_csr_and_key

    def gen(common_name="Kanga Dev", email=""):
        csr, key, extra = original(common_name, email)
        services.certs.append(aps.Certificate(
            serial_number="NEWSERIAL", name="Kanga Dev",
            cert_content=aps.certificate_to_pem(self_signed_cert(key))))
        # compare nella lista solo dopo l'invio della CSR (vedi filtro sotto)
        return csr, key, extra

    monkeypatch.setattr(aps, "generate_csr_and_key", gen)
    real_list = services.list_certificates
    services.list_certificates = lambda team_id: [
        c for c in real_list(team_id)
        if c.serial_number != "NEWSERIAL" or services.csr_submitted
    ]


def test_full_sideload_with_new_certificate(monkeypatch, tmp_path, appdata):
    orphan_key = new_key_pem()
    orphan = aps.Certificate("OLD1", "Vecchio", aps.certificate_to_pem(self_signed_cert(orphan_key)))
    services = FakeServices(certs=[orphan])
    _issue_cert_for_saved_key(services, monkeypatch)
    h = Harness(monkeypatch, tmp_path, services)

    result = h.run(custom_name="Fotocamera", device_name="iPhone di Mario")

    # passi emessi nell'ordine giusto, poi "done"
    assert h.steps() == [k for k, _ in STEPS] + ["done"]
    # device registrato col suo nome, certificato orfano revocato, nuovo certificato salvato
    assert services.registered == [(UDID, "iPhone di Mario")]
    assert services.revoked == ["OLD1"]
    assert services.csr_submitted
    key_path = store.keys_dir() / "private.pem"
    assert key_path.exists()
    new_cert = next(c for c in services.certs if c.serial_number == "NEWSERIAL")
    assert aps.cert_matches_key(new_cert.cert_content, key_path.read_bytes())
    # App ID unico per account, firma con bundle e nome personalizzati
    expected_bundle = _unique_bundle_id("com.example.camera", "000123-ADSID")
    assert services.added_app_ids == [expected_bundle]
    assert h.sign_args["bundle_id"] == expected_bundle
    assert h.sign_args["display_name"] == "Fotocamera"
    # i file temporanei (il .p12 contiene la chiave privata!) vengono rimossi
    assert h.temp_seen and not any(p.exists() for p in h.temp_seen)
    # installazione con eventi di progresso
    assert h.installed == [(UDID, tmp_path / "Camera-kanga.ipa")]
    progress = [d for t, d in h.events if t == "progress"]
    assert progress == [(None, "Caricamento sul dispositivo"), (40, ""), (100, "Completata")]
    assert any("zsign: >>> Signing" in m for m in h.logs())
    assert not any("debug noise" in m for m in h.logs())
    # risultato
    assert result.signed_ipa == tmp_path / "Camera-kanga.ipa"
    assert result.bundle_id == expected_bundle
    assert result.original_bundle_id == "com.example.camera"
    assert result.app_name == "Fotocamera"
    assert result.team_name == "Mario Rossi"
    assert result.expires == EXPIRES


def test_reuses_certificate_matching_saved_key(monkeypatch, tmp_path, appdata):
    key = new_key_pem()
    store.write_private(store.keys_dir() / "private.pem", key)
    other = aps.Certificate("OTHER", "Xcode", aps.certificate_to_pem(self_signed_cert(new_key_pem())))
    mine = aps.Certificate("MINE", "Kanga Dev", aps.certificate_to_pem(self_signed_cert(key)))
    services = FakeServices(certs=[other, mine], devices=[UDID],
                            app_ids=[{"identifier": "*", "appIdId": "WILD"}])
    h = Harness(monkeypatch, tmp_path, services)
    h.run()
    assert not services.csr_submitted and services.revoked == []
    assert services.registered == []            # già registrato
    assert services.added_app_ids == []         # App ID wildcard riusato
    assert any("Uso il certificato esistente: Kanga Dev" in m for m in h.logs())


def test_saved_key_not_matching_any_certificate(monkeypatch, tmp_path, appdata):
    store.write_private(store.keys_dir() / "private.pem", new_key_pem())
    stale = aps.Certificate("STALE", "x", aps.certificate_to_pem(self_signed_cert(new_key_pem())))
    services = FakeServices(certs=[stale])
    _issue_cert_for_saved_key(services, monkeypatch)
    h = Harness(monkeypatch, tmp_path, services)
    h.run()
    # prima il bug: si usava certs[0] con una chiave che non c'entrava -> zsign falliva
    assert services.revoked == ["STALE"] and services.csr_submitted


def test_temp_files_removed_when_signing_fails(monkeypatch, tmp_path, appdata):
    services = FakeServices()
    _issue_cert_for_saved_key(services, monkeypatch)
    h = Harness(monkeypatch, tmp_path, services, sign_error=ZsignError("zsign fallito (exit 1)"))
    with pytest.raises(ZsignError):
        h.run()
    assert h.temp_seen and not any(p.exists() for p in h.temp_seen)
    assert h.installed == []
    assert h.steps()[-1] == "sign"


def test_missing_ipa_and_bad_session(monkeypatch, tmp_path, appdata):
    flow = AppFlow()
    with pytest.raises(AppFlowError, match="IPA non trovata"):
        flow.sideload(AppleSession(xcode_token="x"), tmp_path / "nope.ipa", UDID)


def test_login_stores_apple_id(monkeypatch, appdata):
    class FakeGsa:
        def __init__(self, getter, **kwargs):
            pass

        def login(self, apple_id, password, on_2fa=None):
            return AppleSession(adsid="ADSID-123", dsid="9", xcode_token="XT", cookies={"myacinfo": "c"})

    monkeypatch.setattr(appflow, "GsaClient", FakeGsa)
    flow = AppFlow()
    assert not flow.has_session() and flow.account_name() == ""
    flow.login("mario@icloud.com", "pw")
    assert flow.has_session()
    assert flow.account_name() == "mario@icloud.com"
    assert "pw" not in store.session_path().read_text()
    flow.logout()
    assert not flow.has_session()


def test_login_without_xcode_token_fails(monkeypatch, appdata):
    class FakeGsa:
        def __init__(self, getter, **kwargs):
            pass

        def login(self, apple_id, password, on_2fa=None):
            return AppleSession(adsid="A")

    monkeypatch.setattr(appflow, "GsaClient", FakeGsa)
    with pytest.raises(AppFlowError, match="Xcode token"):
        AppFlow().login("a@b.c", "pw")
    assert store.load_session() is None


def test_unique_bundle_id_is_stable():
    a = _unique_bundle_id("com.example.app", "ADSID")
    assert a == _unique_bundle_id("com.example.app", "adsid")
    assert a.startswith("com.example.") and a.endswith("app") and a != "com.example.app"
    assert _unique_bundle_id(a, "ADSID") == a  # già trasformato: invariato
    assert _unique_bundle_id("single", "ADSID").startswith("single.")
