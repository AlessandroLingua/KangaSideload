"""Orchestratore del flusso completo di sideload.

login -> team -> registrazione device -> certificato -> App ID -> profilo
       -> entitlements -> firma (zsign) -> installazione (pymobiledevice3)

Ogni passo emette eventi verso la callback on_event(tag, message) per il
log della GUI.
"""
from __future__ import annotations

import logging
import plistlib
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

from . import apple_services, device, store
from .anisette_provider import AnisetteProvider
from .entitlements import free_provisioning_entitlements
from .gsa import AppleSession, GsaClient, default_anisette_getter
from .signer import parse_bundle_id_from_ipa, sign_ipa

logger = logging.getLogger(__name__)


class AppFlowError(RuntimeError):
    pass


def _safe_bundle_component(name: str) -> str:
    comp = re.sub(r"[^A-Za-z0-9.-]", "", name) or "app"
    if comp[0].isdigit():
        comp = "x" + comp
    return comp


def _unique_bundle_id(base: str, email: str) -> str:
    """Ridistribuisce il bundle id per account gratuito (come AltStore/Sideloadly):
    aggiunge un componente derivato dall'account per non collidere col profilo."""
    import hashlib

    h = hashlib.sha1(email.lower().encode()).hexdigest()[:5].upper()
    parts = base.rsplit(".", 1)
    if len(parts) == 2:
        return f"{parts[0]}.{h}{parts[1]}" if not parts[1].startswith(h) else base
    return f"{base}.{h}"


class AppFlow:
    def __init__(self, on_event=None):
        self.on_event = on_event or (lambda tag, msg: None)
        self.anisette = AnisetteProvider()

    def log(self, msg: str):
        # NOTA: niente logger.info qui — il bridge GUI ripeterebbe il messaggio
        self.on_event("log", msg)

    # ---------------------------------------------------------------- login
    def login(self, apple_id: str, password: str, on_2fa=None) -> AppleSession:
        self.log(f"Autenticazione con Apple (GSA) per {apple_id}…")
        self.log(f"[ANISETTE] Genero header anisette dalla sessione locale…")
        try:
            headers = default_anisette_getter()
            self.log(f"[ANISETTE] OK: {', '.join(sorted(headers.keys()))}")
        except Exception as e:
            self.log(f"[ANISETTE] ✗ Errore anisette: {e}")
            raise
        client = GsaClient(default_anisette_getter, on_event=self.log)
        apple_session = client.login(apple_id, password, on_2fa=on_2fa)
        if not apple_session.xcode_token:
            raise AppFlowError(
                "Login riuscito ma Xcode token non ottenuto: i servizi di firma non sono disponibili"
            )
        store.save_session(store.session_from_gsa(apple_session))
        self.log(f"Login riuscito: {apple_id} (adsid {apple_session.adsid[:8]}...)")
        return apple_session

    def restore_session(self) -> AppleSession | None:
        data = store.load_session()
        if not data:
            return None
        return store.session_to_apple(data)

    def logout(self):
        store.clear_session()
        self.log("Sessione azzerata")

    # ------------------------------------------------------------- install
    def sideload(
        self,
        apple_session: AppleSession,
        ipa_path: Path,
        udid: str,
        out_dir: Path | None = None,
        custom_name: str | None = None,
    ) -> Path:
        """Flusso completo: prepara credenziali, firma e installa. Ritorna l'IPA firmata."""
        ipa_path = Path(ipa_path)
        if not ipa_path.exists():
            raise AppFlowError(f"IPA non trovata: {ipa_path}")

        bundle_id = parse_bundle_id_from_ipa(ipa_path)
        if not bundle_id:
            raise AppFlowError("CFBundleIdentifier non trovato nell'IPA")

        services = apple_services.AppleServicesClient(apple_session, self.anisette.get_headers)

        # 1. team
        self.log("Ricerca team di sviluppo...")
        teams = services.list_teams()
        if not teams:
            raise AppFlowError("Nessun team di sviluppo associato all'account")
        team = teams[0]
        team_id = team["teamId"]
        self.log(f"Team: {team.get('name', team_id)}")

        # 2. device: registra se assente
        self.log(f"Verifica registrazione device {udid}...")
        known = {d.get("deviceNumber") for d in services.list_devices(team_id)}
        if udid not in known:
            self.log("Registrazione device su Apple...")
            services.register_device(team_id, udid, name=f"Kanga {udid[:6]}")
            self.log("Device registrato (i profili vanno rigenerati dopo la registrazione)")

        # 3. certificato di sviluppo: riusa certificato+chiave salvati, altrimenti
        #    revoca i vecchi e ne emette uno nuovo (approccio AltStore)
        self.log("Verifica certificati di sviluppo...")
        certs = services.list_certificates(team_id)
        key_pem = None
        cert_pem = None
        saved_key_path = store.keys_dir() / "private.pem"
        if saved_key_path.exists() and certs:
            key_pem = saved_key_path.read_bytes()
            cert = certs[0]
            cert_pem = cert.cert_content
            self.log(f"Uso certificato esistente: {cert.name or cert.serial_number}")
        else:
            for cert in certs:
                self.log(f"Revoco certificato orfano {cert.serial_number} (chiave assente)...")
                try:
                    services.revoke_certificate(team_id, cert.serial_number)
                except Exception as e:
                    self.log(f"  revoca fallita (continuo): {e}")
            self.log("Genero CSR e richiedo nuovo certificato...")
            csr_pem, key_pem, _ = apple_services.generate_csr_and_key(common_name="Kanga Dev")
            cert_req = services.submit_csr(team_id, csr_pem, machine_name=f"Kanga-{uuid.uuid4().hex[:6]}")
            cert_pem = cert_req.cert_content
            saved_key_path.write_bytes(key_pem)
            self.log(f"Certificato emesso: {cert_req.serial_number}")

        if not cert_pem:
            raise AppFlowError("Certificato Apple non disponibile")
        if key_pem is None:
            raise AppFlowError("Chiave privata del certificato non disponibile")
        # 4. App ID (limiti free: 10/week -> riusa se esiste)
        new_bundle_id = _unique_bundle_id(bundle_id, apple_session.adsid or apple_session.dsid)
        self.log(f"App ID: {new_bundle_id}")
        app_ids = services.list_app_ids(team_id)
        target = None
        for a in app_ids:
            ident = a.get("identifier", "")
            # il profilo free usa wildcard o exact match col prefisso team
            if ident == new_bundle_id or ident == "*":
                target = a
                break
        if target is None:
            target = services.add_app_id(team_id, new_bundle_id, name=custom_name or "Kanga App")
            self.log("App ID registrato")

        # 5. profilo provisioning
        self.log("Download profilo di provisioning...")
        profile = services.download_profile(team_id, target["appIdId"])

        # 6. entitlements coerenti col profilo free
        team_from_profile = profile.team_id() or team_id
        profile_bundle_ids = profile.bundle_ids()
        sign_bundle_id = new_bundle_id
        if profile_bundle_ids and "*" not in profile_bundle_ids:
            # profilo non wildcard: deve contenere il nostro bundle id
            if new_bundle_id not in profile_bundle_ids:
                sign_bundle_id = profile_bundle_ids[0]
        ents_path = Path(tempfile.gettempdir()) / f"kanga_ent_{uuid.uuid4().hex}.plist"
        ents_path.write_bytes(free_provisioning_entitlements(team_from_profile, sign_bundle_id))

        # 7. p12
        p12_path = Path(tempfile.gettempdir()) / f"kanga_cert_{uuid.uuid4().hex}.p12"
        p12_password = uuid.uuid4().hex
        p12_path.write_bytes(apple_services.build_p12(cert_pem, key_pem, p12_password))

        # 8. firma
        out_dir = Path(out_dir or (ipa_path.parent))
        out_dir.mkdir(parents=True, exist_ok=True)
        signed_ipa = out_dir / f"{ipa_path.stem}-kanga.ipa"
        self.log(f"Firma con zsign -> {signed_ipa.name}...")
        sign_ipa(
            ipa_in=ipa_path,
            ipa_out=signed_ipa,
            p12_path=p12_path,
            p12_password=p12_password,
            mobileprovision_path=self._profile_to_file(profile),
            entitlements_path=ents_path,
            bundle_id=sign_bundle_id,
            display_name=custom_name,
            on_output=lambda line: self.log(f"  zsign: {line}") if line.strip().startswith(">>>") else None,
        )
        self._cleanup([ents_path, p12_path])
        self.log("Firma completata")

        # 9. installazione
        self.log(f"Installazione su {udid}...")
        device.install_ipa(
            udid,
            signed_ipa,
            on_progress=lambda pct, state: self.on_event("progress", f"{pct}% {state}"),
        )
        self.log("Installazione completata: apri l'app sul device (prima avvio: imposta fiducia sviluppatore)")
        return signed_ipa

    @staticmethod
    def _profile_to_file(profile) -> Path:
        p = Path(tempfile.gettempdir()) / f"kanga_prov_{uuid.uuid4().hex}.mobileprovision"
        p.write_bytes(profile.raw)
        return p

    @staticmethod
    def _cleanup(paths: list[Path]):
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
