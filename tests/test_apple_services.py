"""Certificati, chiavi, P12 e profili di provisioning."""
import base64
from datetime import datetime, timezone

from cryptography.hazmat.primitives.serialization import pkcs12

from fakes import fake_mobileprovision, new_key_pem, self_signed_cert
from kanga.core import apple_services as aps


def test_certificate_to_pem_accepts_every_apple_format():
    key = new_key_pem()
    der = self_signed_cert(key)
    pem = aps.certificate_to_pem(der)
    assert pem.startswith("-----BEGIN CERTIFICATE-----")
    assert aps.certificate_to_pem(pem) == pem                          # già PEM (str)
    assert aps.certificate_to_pem(pem.encode()).strip() == pem.strip()  # PEM in bytes
    assert aps.certificate_to_pem(base64.b64encode(der).decode()) == pem  # DER in base64
    assert aps.certificate_to_pem(b"garbage") == ""
    assert aps.certificate_to_pem("not base64!!") == ""
    assert aps.certificate_to_pem(None) == ""


def test_certificate_from_dict_handles_der_and_serialnum():
    der = self_signed_cert(new_key_pem())
    cert = aps.Certificate.from_dict({"serialNum": "ABC123", "name": "iPhone Developer", "certContent": der})
    assert cert.serial_number == "ABC123"
    assert cert.cert_content.startswith("-----BEGIN CERTIFICATE-----")
    # risposta di submitDevelopmentCSR senza contenuto: nessun crash, contenuto vuoto
    assert aps.Certificate.from_dict({"serialNumber": "X"}).cert_content == ""


def test_cert_matches_key_and_p12():
    key_a, key_b = new_key_pem(), new_key_pem()
    pem_a = aps.certificate_to_pem(self_signed_cert(key_a))
    assert aps.cert_matches_key(pem_a, key_a)
    assert not aps.cert_matches_key(pem_a, key_b)
    assert not aps.cert_matches_key("broken", key_a)

    p12 = aps.build_p12(pem_a, key_a, "secret")
    key, cert, _ = pkcs12.load_key_and_certificates(p12, b"secret")
    assert key is not None and cert is not None


def test_provisioning_profile_parsing():
    expires = datetime(2026, 11, 13, 10, 30, tzinfo=timezone.utc)
    prof = aps.ProvisioningProfile(raw=fake_mobileprovision("TEAM123", "com.example.ABCDEapp", expires))
    # regressione: un campo dataclass 'entitlements' oscurava il metodo e team_id() andava in crash
    assert prof.entitlements()["get-task-allow"] is True
    assert prof.team_id() == "TEAM123"
    assert prof.bundle_ids() == ["com.example.ABCDEapp"]
    assert prof.expires() == expires

    empty = aps.ProvisioningProfile()
    assert empty.team_id() == "" and empty.bundle_ids() == [] and empty.expires() is None
    assert aps.ProvisioningProfile(raw=b"<?xml broken </plist>").parsed() == {}


def test_csr_generation():
    csr, key, _ = aps.generate_csr_and_key("Kanga Dev")
    assert csr.startswith("-----BEGIN CERTIFICATE REQUEST-----")
    assert key.startswith(b"-----BEGIN PRIVATE KEY-----")
