"""Client Apple Developer Services (developerservices2.apple.com).

Usa lo Xcode token mintato da gsa.py (X-Apple-GS-Token + X-Apple-I-Identity-Id)
per guidare la stessa API del developer portal che usa Xcode: team, certificati
di sviluppo da CSR, registrazione device, App ID e profilo di provisioning.
Questi passi permettono a un account Apple gratuito di firmare un'app per un
device specifico (limiti Apple: 7 giorni di validità, max 3 app per device,
10 App ID per settimana).

Endpoint verificati contro le implementazioni open di riferimento
(EmLoadServer MIT, AltStore AGPL, Dadoum/Sideloader GPL).
"""
from __future__ import annotations

import base64
import logging
import plistlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE = "https://developerservices2.apple.com/services/QH65B2/"
_CLIENT_ID = "XABBG36SBA"
_PROTOCOL_VERSION = "QH65B2"
_USER_AGENT = "Xcode"
_XCODE_VERSION = "11.2 (11B41)"
_APP_INFO = "com.apple.gs.xcode.auth"
_TIMEOUT = 30


class DeveloperServicesError(RuntimeError):
    pass


@dataclass
class DeviceRegistration:
    udid: str
    name: str
    device_id: str = ""  # id Apple del device registrato


@dataclass
class Certificate:
    serial_number: str
    name: str = ""
    cert_content: str = ""  # PEM
    expiration_date: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Certificate":
        return cls(
            serial_number=str(d.get("serialNumber") or d.get("serialNum") or ""),
            name=str(d.get("name") or d.get("commonName") or ""),
            # Apple restituisce certContent come <data> (DER): lo normalizziamo in PEM
            cert_content=certificate_to_pem(d.get("certContent")),
            expiration_date=str(d.get("expirationDate", "")),
        )


@dataclass
class ProvisioningProfile:
    """Profilo .mobileprovision scaricato da Apple."""

    name: str = ""
    uuid: str = ""
    app_id_name: str = ""  # TEAMID.bundleid
    expiration_date: str = ""
    raw: bytes = b""  # il file .mobileprovision completo, pronto per zsign

    @classmethod
    def from_apple(cls, d: dict) -> "ProvisioningProfile":
        return cls(
            name=str(d.get("name", "")),
            uuid=str(d.get("uuid", "")),
            app_id_name=str(d.get("appIdName", "")),
            expiration_date=str(d.get("expirationDate", "")),
            raw=d.get("encodedProfile", b"") or b"",
        )

    def parsed(self) -> dict:
        """Parsa il contenuto plist del .mobileprovision."""
        if not self.raw:
            return {}
        data = self.raw
        # il .mobileprovision è un CMS: estrai il plist tra <?xml e </plist>
        start = data.find(b"<?xml")
        end = data.find(b"</plist>")
        if start == -1 or end == -1:
            return {}
        try:
            parsed = plistlib.loads(data[start : end + 8])
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def expires(self) -> datetime | None:
        """Scadenza del profilo (UTC): con un account gratuito ~7 giorni."""
        value = self.parsed().get("ExpirationDate")
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None

    def entitlements(self) -> dict:
        return self.parsed().get("Entitlements", {})

    def team_id(self) -> str:
        return str(self.entitlements().get("com.apple.developer.team-identifier", ""))

    def bundle_ids(self) -> list[str]:
        app_id = self.entitlements().get("application-identifier", "")
        if "." in app_id:
            return [app_id.split(".", 1)[1]]
        return []


class AppleServicesClient:
    def __init__(self, session, anisette_getter, timeout: float = _TIMEOUT):
        """session: AppleSession (da gsa.py) con adsid + xcode_token."""
        if not session.xcode_token:
            raise DeveloperServicesError(
                "Sessione senza Xcode token: serve prima il login GSA riuscito"
            )
        self._session = session
        self._get_anisette = anisette_getter
        self._timeout = timeout
        self._http = requests.Session()
        self._http.verify = _ca_bundle()

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "text/x-xml-plist",
            "User-Agent": _USER_AGENT,
            "Accept": "text/x-xml-plist",
            "Accept-Language": "en-us",
            "X-Apple-App-Info": _APP_INFO,
            "X-Xcode-Version": _XCODE_VERSION,
            "X-Apple-I-Identity-Id": self._session.adsid,
            "X-Apple-GS-Token": self._session.xcode_token,
        }
        try:
            h.update(self._get_anisette())
        except Exception as e:
            logger.debug("anisette non disponibile per developerservices2: %s", e)
        return h

    def _request(self, endpoint: str, params: dict | None = None, team_id: str | None = None) -> dict:
        body: dict[str, Any] = {
            "clientId": _CLIENT_ID,
            "protocolVersion": _PROTOCOL_VERSION,
            "requestId": str(uuid.uuid4()).upper(),
        }
        if team_id:
            body["teamId"] = team_id
        if params:
            body.update(params)
        url = f"{_BASE}{endpoint}?clientId={_CLIENT_ID}"
        resp = self._http.post(
            url,
            headers=self._headers(),
            data=plistlib.dumps(body, fmt=plistlib.FMT_XML),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        result = plistlib.loads(resp.content)
        code = result.get("resultCode")
        if code not in (0, None, "0"):
            message = result.get("userString") or result.get("resultString") or "errore sconosciuto"
            raise DeveloperServicesError(f"Apple resultCode {code}: {message}")
        return result

    # ------------------------------------------------------------------ team
    def list_teams(self) -> list[dict]:
        return self._request("listTeams.action").get("teams", [])

    # ---------------------------------------------------------------- device
    def list_devices(self, team_id: str) -> list[dict]:
        return self._request("ios/listDevices.action", team_id=team_id).get("devices", [])

    def register_device(self, team_id: str, udid: str, name: str = "Kanga device") -> dict:
        name = "".join(ch for ch in name if ch.isalnum() or ch == " ").strip() or "Kanga device"
        result = self._request(
            "ios/addDevice.action", {"deviceNumber": udid, "name": name}, team_id=team_id
        )
        return result.get("device", {})

    # ----------------------------------------------------------- certificati
    def list_certificates(self, team_id: str) -> list[Certificate]:
        certs = self._request("ios/listAllDevelopmentCerts.action", team_id=team_id).get(
            "certificates", []
        )
        return [Certificate.from_dict(c) for c in certs]

    def submit_csr(self, team_id: str, csr_pem: str, machine_name: str = "Kanga") -> Certificate:
        result = self._request(
            "ios/submitDevelopmentCSR.action",
            {"csrContent": csr_pem, "machineId": str(uuid.uuid4()), "machineName": machine_name},
            team_id=team_id,
        )
        return Certificate.from_dict(result.get("certRequest", {}))

    def revoke_certificate(self, team_id: str, serial_number: str) -> None:
        self._request(
            "ios/revokeDevelopmentCert.action", {"serialNumber": serial_number}, team_id=team_id
        )

    # --------------------------------------------------------------- app id
    def list_app_ids(self, team_id: str) -> list[dict]:
        return self._request("ios/listAppIds.action", team_id=team_id).get("appIds", [])

    def add_app_id(self, team_id: str, bundle_id: str, name: str = "Kanga App") -> dict:
        name = "".join(ch for ch in name if ch.isalnum() or ch == " ").strip() or "Kanga App"
        result = self._request(
            "ios/addAppId.action", {"identifier": bundle_id, "name": name}, team_id=team_id
        )
        return result.get("appId", {})

    def delete_app_id(self, team_id: str, app_id_id: str) -> None:
        """Libera uno dei 10 slot App ID settimanali dell'account gratuito."""
        self._request("ios/deleteAppId.action", {"appIdId": app_id_id}, team_id=team_id)

    # ------------------------------------------------------------- profilo
    def download_profile(self, team_id: str, app_id_id: str) -> ProvisioningProfile:
        result = self._request(
            "ios/downloadTeamProvisioningProfile.action", {"appIdId": app_id_id}, team_id=team_id
        )
        profile = ProvisioningProfile.from_apple(result.get("provisioningProfile", {}))
        if not profile.raw:
            raise DeveloperServicesError("Profilo di provisioning vuoto")
        return profile


def _ca_bundle() -> str:
    """I servizi Apple usano la PKI Apple: usa il bundle del pacchetto anisette."""
    from .gsa import apple_ca_bundle

    return apple_ca_bundle()


def generate_csr_and_key(common_name: str = "Kanga Dev", email: str = "") -> tuple[str, bytes, bytes]:
    """Genera coppia RSA 2048 + CSR (PEM). Ritorna (csr_pem, key_pem, cert_placeholder).

    La chiave privata va unita al certificato Apple per creare il .p12 per zsign.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
            x509.NameAttribute(NameOID.EMAIL_ADDRESS, email or common_name),
        ]
    )
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(name)
        .sign(key, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return csr_pem, key_pem, b""


def certificate_to_pem(content) -> str:
    """Normalizza un certificato (DER in bytes, PEM, o DER in base64) in PEM. "" se assente/invalido."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    if not content:
        return ""
    if isinstance(content, (bytes, bytearray)):
        data = bytes(content)
        if data.lstrip().startswith(b"-----BEGIN"):
            return data.decode("ascii", errors="ignore").strip() + "\n"
        der = data
    else:
        text = str(content).strip()
        if text.startswith("-----BEGIN"):
            return text + "\n"
        try:
            der = base64.b64decode(text, validate=True)
        except Exception:
            return ""
    try:
        cert = x509.load_der_x509_certificate(der)
    except Exception:
        return ""
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def cert_matches_key(cert_pem: str, key_pem: bytes) -> bool:
    """True se la chiave privata corrisponde alla chiave pubblica del certificato."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        key = serialization.load_pem_private_key(key_pem, password=None)
    except Exception:
        return False
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert.public_key().public_bytes(*fmt) == key.public_key().public_bytes(*fmt)


def build_p12(cert_pem: str, key_pem: bytes, p12_password: str) -> bytes:
    """Crea il pacchetto PKCS#12 (certificato Apple + chiave privata) per zsign."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    key = serialization.load_pem_private_key(key_pem, password=None)
    return pkcs12.serialize_key_and_certificates(
        name=b"Kanga",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(p12_password.encode()),
    )
