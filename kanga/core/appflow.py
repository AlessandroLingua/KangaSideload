"""Orchestratore del flusso completo di sideload.

login -> team -> registrazione device -> certificato -> App ID -> profilo
       -> entitlements -> firma (zsign) -> installazione (pymobiledevice3)

Ogni passo emette eventi verso la callback on_event(tag, data):

- ("log", str)                     riga per il registro
- ("step", str)                    chiave del passo corrente (vedi STEPS) o "done"
- ("progress", (int | None, str))  avanzamento installazione sul device

La callback viene invocata dal thread che esegue il flusso: la GUI la
inoltra al thread principale tramite un segnale Qt.
"""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import apple_services, device, store
from .anisette_provider import AutoAnisette
from .entitlements import free_provisioning_entitlements
from .gsa import AppleSession, GsaClient
from .ipa import read_ipa_info
from .signer import sign_ipa

logger = logging.getLogger(__name__)

# passi mostrati nella GUI, nell'ordine in cui vengono eseguiti
STEPS: list[tuple[str, str]] = [
    ("account", "Account"),
    ("device", "Dispositivo"),
    ("certificate", "Certificato"),
    ("appid", "App ID"),
    ("profile", "Profilo"),
    ("sign", "Firma"),
    ("install", "Installazione"),
]

FREE_PROFILE_DAYS = 7


class AppFlowError(RuntimeError):
    pass


@dataclass
class SideloadResult:
    signed_ipa: Path
    bundle_id: str           # bundle id con cui l'app è stata firmata
    original_bundle_id: str
    app_name: str
    udid: str
    team_name: str = ""
    expires: datetime | None = None


def _safe_bundle_component(name: str) -> str:
    comp = re.sub(r"[^A-Za-z0-9.-]", "", name) or "app"
    if comp[0].isdigit():
        comp = "x" + comp
    return comp


def _unique_bundle_id(base: str, account_key: str) -> str:
    """Ridistribuisce il bundle id per account gratuito (come AltStore/Sideloadly):
    aggiunge un componente derivato dall'account per non collidere col profilo."""
    h = hashlib.sha1(account_key.lower().encode()).hexdigest()[:5].upper()
    parts = base.rsplit(".", 1)
    if len(parts) == 2:
        return f"{parts[0]}.{h}{parts[1]}" if not parts[1].startswith(h) else base
    return f"{base}.{h}"


class AppFlow:
    def __init__(self, on_event=None, anisette=None):
        self.on_event = on_event or (lambda tag, data: None)
        # stessa fonte anisette per login e servizi sviluppatore (iTunes o emulata)
        self.anisette = anisette or AutoAnisette()

    # ------------------------------------------------------------- eventi
    def _emit(self, tag: str, data) -> None:
        try:
            self.on_event(tag, data)
        except Exception:
            logger.debug("callback on_event fallita", exc_info=True)

    def log(self, msg: str):
        logger.info(msg)
        self._emit("log", msg)

    def step(self, key: str):
        self._emit("step", key)

    # ---------------------------------------------------------------- login
    def _logged_anisette(self):
        """Getter anisette che annota nel registro la fonte usata (una volta per login)."""
        announced = False

        def get() -> dict:
            nonlocal announced
            try:
                headers = self.anisette.get_headers()
            except Exception as e:
                self.log(f"[ANISETTE] Errore: {e}")
                raise
            if not announced:
                announced = True
                source = getattr(self.anisette, "source", "") or "?"
                self.log(f"[ANISETTE] Fonte: {source}")
                reason = getattr(self.anisette, "fallback_reason", "")
                if reason:
                    self.log(f"[ANISETTE] iTunes non disponibile: {reason}")
            return headers

        return get

    def login(self, apple_id: str, password: str, on_2fa=None) -> AppleSession:
        self.log(f"Autenticazione con Apple (GSA) per {apple_id}…")
        client = GsaClient(self._logged_anisette(), on_event=self.log)
        apple_session = client.login(apple_id, password, on_2fa=on_2fa)
        if not apple_session.xcode_token:
            raise AppFlowError(
                "Login riuscito ma Xcode token non ottenuto: i servizi di firma non sono disponibili"
            )
        store.save_session(store.session_from_gsa(apple_session, apple_id=apple_id))
        self.log(f"Login riuscito: {apple_id} (adsid {apple_session.adsid[:8]}...)")
        return apple_session

    def restore_session(self) -> AppleSession | None:
        data = store.load_session()
        if not data:
            return None
        try:
            return store.session_to_apple(data)
        except Exception as e:
            logger.warning("Sessione salvata non valida (%s): la ignoro", e)
            return None

    def has_session(self) -> bool:
        session = self.restore_session()
        return bool(session and session.xcode_token)

    def account_name(self) -> str:
        """Apple ID della sessione salvata ('' se sconosciuto o assente)."""
        return str((store.load_session() or {}).get("apple_id") or "")

    def logout(self):
        store.clear_session()
        self.log("Sessione azzerata")

    # ---------------------------------------------------------- certificato
    def _prepare_certificate(self, services, team_id: str) -> tuple[str, bytes]:
        """Riusa il certificato che corrisponde alla chiave salvata, altrimenti ne
        emette uno nuovo revocando quelli inutilizzabili (approccio AltStore)."""
        certs = services.list_certificates(team_id)
        key_path = store.keys_dir() / "private.pem"

        if key_path.exists():
            key_pem = key_path.read_bytes()
            for cert in certs:
                if cert.cert_content and apple_services.cert_matches_key(cert.cert_content, key_pem):
                    self.log(f"Uso il certificato esistente: {cert.name or cert.serial_number}")
                    return cert.cert_content, key_pem
            if certs:
                self.log("La chiave salvata non corrisponde a nessun certificato attivo")

        for cert in certs:
            self.log(f"Revoco il certificato {cert.serial_number} (chiave non disponibile)...")
            try:
                services.revoke_certificate(team_id, cert.serial_number)
            except Exception as e:
                self.log(f"  revoca fallita (continuo): {e}")

        self.log("Genero la richiesta (CSR) e chiedo un nuovo certificato ad Apple...")
        csr_pem, key_pem, _ = apple_services.generate_csr_and_key(common_name="Kanga Dev")
        cert_req = services.submit_csr(team_id, csr_pem, machine_name=f"Kanga-{uuid.uuid4().hex[:6]}")
        cert_pem = cert_req.cert_content
        if not cert_pem and cert_req.serial_number:
            # Apple spesso risponde solo col seriale: il contenuto è nella lista certificati
            for cert in services.list_certificates(team_id):
                if cert.serial_number == cert_req.serial_number and cert.cert_content:
                    cert_pem = cert.cert_content
                    break
        if not cert_pem:
            raise AppFlowError("Apple non ha restituito il certificato appena emesso")
        store.write_private(key_path, key_pem)
        self.log(f"Certificato emesso: {cert_req.serial_number}")
        return cert_pem, key_pem

    # ------------------------------------------------------------- install
    def sideload(
        self,
        apple_session: AppleSession,
        ipa_path: Path,
        udid: str,
        out_dir: Path | None = None,
        custom_name: str | None = None,
        device_name: str | None = None,
    ) -> SideloadResult:
        """Flusso completo: prepara credenziali, firma e installa."""
        ipa_path = Path(ipa_path)
        if not ipa_path.exists():
            raise AppFlowError(f"IPA non trovata: {ipa_path}")
        info = read_ipa_info(ipa_path)
        bundle_id = info.bundle_id
        app_name = custom_name or info.name

        services = apple_services.AppleServicesClient(apple_session, self.anisette.get_headers)

        # 1. team
        self.step("account")
        self.log("Ricerca team di sviluppo...")
        teams = services.list_teams()
        if not teams:
            raise AppFlowError("Nessun team di sviluppo associato all'account")
        team = teams[0]
        team_id = team["teamId"]
        team_name = str(team.get("name") or team_id)
        self.log(f"Team: {team_name}")

        # 2. device: registra se assente
        self.step("device")
        self.log(f"Verifica registrazione dispositivo {udid}...")
        known = {d.get("deviceNumber") for d in services.list_devices(team_id)}
        if udid not in known:
            self.log("Registrazione del dispositivo su Apple...")
            services.register_device(team_id, udid, name=device_name or f"Kanga {udid[:6]}")
            self.log("Dispositivo registrato")

        # 3. certificato di sviluppo
        self.step("certificate")
        self.log("Verifica certificati di sviluppo...")
        cert_pem, key_pem = self._prepare_certificate(services, team_id)

        # 4. App ID (limiti free: 10/settimana -> riusa se esiste)
        self.step("appid")
        new_bundle_id = _unique_bundle_id(bundle_id, apple_session.adsid or apple_session.dsid)
        self.log(f"App ID: {new_bundle_id}")
        target = None
        for a in services.list_app_ids(team_id):
            ident = a.get("identifier", "")
            # il profilo free usa wildcard o exact match col prefisso team
            if ident == new_bundle_id or ident == "*":
                target = a
                break
        if target is None:
            target = services.add_app_id(team_id, new_bundle_id, name=app_name or "Kanga App")
            self.log("App ID registrato")

        # 5. profilo provisioning
        self.step("profile")
        self.log("Download del profilo di provisioning...")
        profile = services.download_profile(team_id, target["appIdId"])
        expires = profile.expires() or (
            datetime.now(timezone.utc) + timedelta(days=FREE_PROFILE_DAYS)
        )

        # 6. entitlements coerenti col profilo free
        team_from_profile = profile.team_id() or team_id
        profile_bundle_ids = profile.bundle_ids()
        sign_bundle_id = new_bundle_id
        if profile_bundle_ids and "*" not in profile_bundle_ids:
            # profilo non wildcard: deve contenere il nostro bundle id
            if new_bundle_id not in profile_bundle_ids:
                sign_bundle_id = profile_bundle_ids[0]

        out_dir = Path(out_dir or ipa_path.parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        signed_ipa = out_dir / f"{ipa_path.stem}-kanga.ipa"

        tmp = Path(tempfile.gettempdir())
        token = uuid.uuid4().hex
        ents_path = tmp / f"kanga_ent_{token}.plist"
        p12_path = tmp / f"kanga_cert_{token}.p12"
        prov_path = tmp / f"kanga_prov_{token}.mobileprovision"
        try:
            ents_path.write_bytes(free_provisioning_entitlements(team_from_profile, sign_bundle_id))
            p12_password = uuid.uuid4().hex
            store.write_private(p12_path, apple_services.build_p12(cert_pem, key_pem, p12_password))
            prov_path.write_bytes(profile.raw)

            # 7. firma
            self.step("sign")
            self.log(f"Firma con zsign -> {signed_ipa.name}...")
            sign_ipa(
                ipa_in=ipa_path,
                ipa_out=signed_ipa,
                p12_path=p12_path,
                p12_password=p12_password,
                mobileprovision_path=prov_path,
                entitlements_path=ents_path,
                bundle_id=sign_bundle_id,
                display_name=custom_name,
                on_output=lambda line: self.log(f"  zsign: {line}") if line.strip().startswith(">>>") else None,
            )
        finally:
            # il .p12 contiene la chiave privata: non deve restare in %TEMP%
            self._cleanup([ents_path, p12_path, prov_path])
        self.log("Firma completata")

        # 8. installazione
        self.step("install")
        self.log(f"Installazione su {device_name or udid}...")
        device.install_ipa(
            udid,
            signed_ipa,
            on_progress=lambda pct, state: self._emit("progress", (pct, state)),
        )
        self.step("done")
        self.log("Installazione completata: apri l'app sul dispositivo "
                 "(al primo avvio autorizza lo sviluppatore nelle Impostazioni)")
        return SideloadResult(
            signed_ipa=signed_ipa,
            bundle_id=sign_bundle_id,
            original_bundle_id=bundle_id,
            app_name=app_name,
            udid=udid,
            team_name=team_name,
            expires=expires,
        )

    @staticmethod
    def _cleanup(paths: list[Path]):
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
