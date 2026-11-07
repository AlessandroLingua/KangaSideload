"""Client di autenticazione Apple GrandSlam (GSA) per Apple ID.

Implementa il protocollo documentato su The Apple Wiki
(https://theapplewiki.com/wiki/Grand_Slam_Authentication):

  1. POST /grandslam/GsService2  {o: init,  u, A2k, ps:[s2k,s2k_fo], cpd}
  2. POST /grandslam/GsService2  {o: complete, u, c, M1, cpd}
     -> M2 + spd (cifrata: AES-256-CBC con chiavi HMAC derivate dalla
        session key SRP)
  3. (se 2FA) GET /auth/verify/trusteddevice per far arrivare il codice
     sul device fidato, poi GET /grandslam/GsService2/validate con header
     `security-code`, infine si ri-esegue init/complete per ottenere i token.

Credenziali: mai salvate. La sessione risultante (cookies + dsid + token)
viene restituita al chiamante che la persiste con store.py.

SRP-6a: variante Apple — SHA-256, gruppo 2048 (RFC5054), x senza username,
password pre-hashata con s2k (PBKDF2-HMAC-SHA256 su SHA256(password),
oppure la variante "s2k_fo" con hex-intermediario).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import platform
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import plistlib
import requests

from . import _vendor  # noqa: F401  (fa registrare il package vendorizzato)
from ._vendor import srp
from ._vendor.srp import NG_2048, SHA256

logger = logging.getLogger(__name__)

GSA_URL = "https://gsa.apple.com/grandslam/GsService2"
GSA_VERIFY_TRUSTED_DEVICE = "https://gsa.apple.com/auth/verify/trusteddevice"
GSA_VALIDATE = "https://gsa.apple.com/grandslam/GsService2/validate"

USER_AGENT = "akd/1.0 CFNetwork/1408.0.4 Darwin/22.5.0"

# NEW_CONNECTION_NOTE: GSA "lega" una connessione keep-alive a un nodo interno; se
# quel nodo inizia a fallire, tutte le richieste successive sulla stessa connessione
# ricevono 5xx (tipico: init 200, complete 200, apptokens 503). Per questo ogni
# richiesta usa una connessione nuova ("Connection: close") e apptokens, che non
# reinvia la password, viene ripetuta sui 5xx temporanei (come AltSign/xtool/iloader).
APPTOKENS_ATTEMPTS = 4
RETRYABLE_STATUS = {502, 503, 504}
CLIENT_INFO = "<MacBookPro18,3> <Mac OS X;13.4.1;22F8> <com.apple.AOSKit/282 (com.apple.dt.Xcode/3594.4.19)>"


class GsaError(RuntimeError):
    """Errore di autenticazione GrandSlam (credenziali, Apple anisette, rete)."""

    def __init__(self, message: str, apple_code: int | None = None, needs_2fa: bool = False,
                 http_status: int | None = None):
        super().__init__(message)
        self.apple_code = apple_code
        self.needs_2fa = needs_2fa
        self.http_status = http_status


@dataclass
class AppleSession:
    """Sessione autenticata: cookies Apple + dati account decifrati."""

    cookies: dict[str, str] = field(default_factory=dict)
    dsid: str = ""
    adsid: str = ""
    tokens: dict[str, str] = field(default_factory=dict)  # es. com.apple.gs.idms.pet
    account_info: dict = field(default_factory=dict)  # spd decifrata completa
    sk: bytes = b""  # session key per lo scambio apptokens (base64 in spd)
    xcode_token: str = ""  # X-Apple-GS-Token (com.apple.gs.xcode.auth)

    @property
    def logged_in(self) -> bool:
        return bool(self.cookies.get("myacinfo"))


def s2k_hash(password: str, salt: bytes, iterations: int, protocol: str) -> bytes:
    """Derivazione password Apple (s2k / s2k_fo).

    s2k:     SHA256(password) -> PBKDF2-HMAC-SHA256(salt, iterations, 32 byte)
    s2k_fo:  come sopra ma con l'hash esadecimale ASCII come password PBKDF2
    """
    p = hashlib.sha256(password.encode("utf-8")).digest()
    if protocol == "s2k_fo":
        p = p.hex().encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", p, salt, iterations, 32)


def _hmac_session_key(session_key: bytes, name: str) -> bytes:
    return hmac.new(session_key, name.encode(), hashlib.sha256).digest()


def decrypt_spd(session_key: bytes, spd: bytes) -> bytes:
    """Decifra il blob 'spd' (AES-256-CBC + PKCS7)."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = _hmac_session_key(session_key, "extra data key:")
    iv = _hmac_session_key(session_key, "extra data iv:")[:16]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(spd) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def apple_ca_bundle() -> str:
    """Percorso dei root CA Apple (gsa.apple.com usa la PKI Apple, non CA pubbliche).

    Riutilizza il bundle che il pacchetto 'anisette' include già.
    """
    import anisette as _anisette
    from pathlib import Path

    pem = Path(_anisette.__file__).parent / "apple-root.pem"
    if not pem.exists():
        raise GsaError("apple-root.pem non trovato nel pacchetto anisette")
    return str(pem)


class GsaClient:
    def __init__(self, anisette_getter, timeout: float = 30.0, on_event=None):
        """anisette_getter: callable -> dict con gli header anisette.
        on_event: callable(str) per mostrare ogni passo nel log della GUI."""
        self._get_anisette = anisette_getter
        self._timeout = timeout
        self._on_event = on_event or (lambda msg: None)
        self._sleep = time.sleep  # attesa tra i tentativi (sostituibile nei test)
        self._session = requests.Session()
        self._session.verify = apple_ca_bundle()
        self._local_user_id = str(uuid.uuid4()).upper()
        self._device_id = str(uuid.uuid4()).upper()

    def _notify(self, msg: str):
        try:
            self._on_event(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------ HTTP
    def _meta_headers(self) -> dict[str, str]:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        locale = "it_IT"
        return {
            "X-Apple-I-Client-Time": now,
            "X-Apple-I-TimeZone": "Europe/Rome",
            "X-Apple-Locale": locale,
            "loc": locale,
            "X-Apple-I-MD-RINFO": "17106176",
            "X-Apple-I-MD-LU": base64.b64encode(self._local_user_id.encode()).decode(),
            "X-Mme-Device-Id": self._device_id,
            "X-Apple-I-SRL-NO": "0",
        }

    def _cpd(self, anisette: dict | None = None) -> dict:
        """Client Provided Data: meta + anisette uniti (come richiede GSA)."""
        cpd = {
            "bootstrap": True,
            "icscrec": True,
            "pbe": False,
            "prkgen": True,
            "svct": "iCloud",
        }
        cpd.update(self._meta_headers())
        cpd.update(anisette if anisette is not None else self._get_anisette())
        return cpd

    @staticmethod
    def client_info(anisette: dict) -> str:
        """X-MMe-Client-Info coerente con la fonte anisette (iTunes/AuthKitWin o emulata)."""
        return anisette.get("X-Mme-Client-Info") or anisette.get("X-MMe-Client-Info") or CLIENT_INFO

    def _gsa_request(self, params: dict) -> dict:
        action = str(params.get("o", "?"))
        self._notify(f"[GSA] → POST gsa.apple.com/grandslam/GsService2 (azione: {action})…")
        anisette = self._get_anisette()
        body = {
            "Header": {"Version": "1.0.1"},
            "Request": {"cpd": self._cpd(anisette), **params},
        }
        headers = {
            "Content-Type": "text/x-xml-plist",
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
            "X-MMe-Client-Info": self.client_info(anisette),
            "Connection": "close",  # vedi NEW_CONNECTION_NOTE
        }
        resp = self._session.post(
            GSA_URL,
            headers=headers,
            data=plistlib.dumps(body, fmt=plistlib.FMT_XML),
            timeout=self._timeout,
        )
        self._notify(f"[GSA] ← risposta HTTP {resp.status_code}")
        if resp.status_code != 200:
            self._notify(f"[GSA] ✗ Apple ha risposto HTTP {resp.status_code} ({action})")
            if action == "apptokens":
                message = (f"Apple ha risposto HTTP {resp.status_code} alla richiesta del token "
                           "di firma (apptokens)")
            elif resp.status_code == 503:
                message = (f"Apple ha risposto HTTP 503 a GsService2 ({action}): succede quando "
                           "Apple rifiuta l'anisette usata (errori -80045/-80046) o quando i suoi "
                           "server sono sovraccarichi")
            else:
                message = f"Risposta inattesa da GsService2 ({action}): HTTP {resp.status_code}"
            raise GsaError(message, http_status=resp.status_code)
        data = plistlib.loads(resp.content)
        if "Response" not in data:
            raise GsaError("Risposta GSA non valida (nessun campo Response)")
        response = data["Response"]
        self._notify(f"[GSA] ← campi risposta: {', '.join(sorted(response.keys()))}")
        status = response.get("Status") or {}
        if status:
            self._notify(
                f"[GSA] ← Status: ec={status.get('ec')} em={str(status.get('em'))[:60]!r}"
            )
        return response

    def _anisette_http_headers(self) -> dict[str, str]:
        """Meta + anisette come header HTTP, con un solo X-MMe-Client-Info coerente."""
        anisette = self._get_anisette()
        headers = dict(self._meta_headers())
        headers.update({k: v for k, v in anisette.items() if k.lower() != "x-mme-client-info"})
        headers["X-MMe-Client-Info"] = self.client_info(anisette)
        headers["Connection"] = "close"  # vedi NEW_CONNECTION_NOTE
        return headers

    # --------------------------------------------------------------- 2FA
    def trigger_2fa(self) -> None:
        """Chiede ad Apple di inviare il codice 2FA sui device fidati."""
        identity_token = self._identity_token()
        headers = {
            "Content-Type": "text/x-xml-plist",
            "User-Agent": "Xcode",
            "Accept": "text/x-xml-plist",
            "Accept-Language": "en-us",
            "X-Apple-Identity-Token": identity_token,
            "X-Apple-App-Info": "com.apple.gs.xcode.auth",
            "X-Xcode-Version": "11.2 (11B41)",
        }
        headers.update(self._anisette_http_headers())
        resp = self._session.get(GSA_VERIFY_TRUSTED_DEVICE, headers=headers, timeout=self._timeout)
        logger.info("2FA: codice inviato ai dispositivi fidati (HTTP %s)", resp.status_code)

    def submit_2fa_code(self, code: str) -> None:
        """Valida il codice a 6 cifre ricevuto dal device fidato."""
        identity_token = self._identity_token()
        headers = {
            "Content-Type": "text/x-xml-plist",
            "User-Agent": "Xcode",
            "Accept": "text/x-xml-plist",
            "Accept-Language": "en-us",
            "X-Apple-Identity-Token": identity_token,
            "X-Apple-App-Info": "com.apple.gs.xcode.auth",
            "X-Xcode-Version": "11.2 (11B41)",
            "security-code": code.strip(),
        }
        headers.update(self._anisette_http_headers())
        resp = self._session.get(GSA_VALIDATE, headers=headers, timeout=self._timeout)
        if not resp.ok:
            raise GsaError(f"Codice 2FA rifiutato (HTTP {resp.status_code}). Riprova con un nuovo codice.")

    def _identity_token(self) -> str:
        dsid = getattr(self, "_pending_dsid", "")
        idms_token = getattr(self, "_pending_idms_token", "")
        if not dsid or not idms_token:
            raise GsaError("Token 2FA non disponibili: esegui prima il login")
        return base64.b64encode(f"{dsid}:{idms_token}".encode()).decode()

    # --------------------------------------------------------------- login
    def login(self, apple_id: str, password: str, on_2fa=None) -> AppleSession:
        """Login completo. on_2fa(callable submit_code) è il callback della GUI
        che raccoglie il codice 2FA dall'utente e lo passa a submit_2fa_code.

        Se Apple richiede 2FA e on_2fa è None, solleva GsaError(needs_2fa=True).
        """
        for attempt in (1, 2):
            session = self._login_once(apple_id, password)
            if session is not None:
                return session
            # 2FA richiesta
            if on_2fa is None:
                raise GsaError(
                    "Autenticazione a due fattori richiesta", needs_2fa=True
                )
            on_2fa(self)  # la GUI mostra il dialog e chiama client.submit_2fa_code(code)
            logger.info("2FA validata: riprovo il login (tentativo %d)", attempt)
        raise GsaError("Login non riuscito dopo la validazione 2FA")

    def _login_once(self, apple_id: str, password: str) -> AppleSession | None:
        """Un ciclo SRP init/complete. None = serve 2FA."""
        srp.rfc5054_enable()
        srp.no_username_in_x()

        self._notify("[SRP] Genero chiave effimera A…")
        usr = srp.User(apple_id, b"", hash_alg=SHA256, ng_type=NG_2048)
        _, A = usr.start_authentication()
        self._notify(f"[SRP] A generata ({len(A)} byte), invio init…")

        resp = self._gsa_request({
            "A2k": A,
            "ps": ["s2k", "s2k_fo"],
            "u": apple_id,
            "o": "init",
        })

        if "sp" not in resp:
            status = resp.get("Status", {})
            raise GsaError(
                f"Init SRP fallito: {status.get('em', resp)}",
                apple_code=status.get("ec"),
            )
        protocol = resp["sp"]
        if protocol not in ("s2k", "s2k_fo"):
            raise GsaError(f"Protocollo password non supportato: {protocol}")

        salt = resp["s"]
        B = resp["B"]
        c = resp["c"]
        iterations = int(resp["i"])
        self._notify(
            f"[SRP] Server: protocollo={protocol}, iterazioni={iterations}, "
            f"salt={len(salt)}B, B={len(B)}B"
        )

        self._notify(f"[SRP] Derivo password con s2k ({iterations} iterazioni)…")
        usr.p = s2k_hash(password, salt, iterations, protocol)
        M1 = usr.process_challenge(salt, B)
        if M1 is None:
            raise GsaError("Challenge SRP non valida (risposta server sospetta)")
        self._notify("[SRP] M1 calcolato, invio complete…")

        resp = self._gsa_request({
            "c": c,
            "M1": M1,
            "u": apple_id,
            "o": "complete",
        })

        if "M2" not in resp:
            status = resp.get("Status", {})
            if "au" in status:
                # 2FA richiesta: salva i token necessari per il flusso 2FA
                self._pending_dsid = resp.get("dsid", "")
                self._pending_idms_token = status.get("idmsToken", "")
                self._notify("[2FA] Apple richiede il codice 2FA (au presente)")
                logger.info("Apple richiede 2FA")
                return None
            raise GsaError(
                f"Login fallito: {status.get('em', 'credenziali non valide')}",
                apple_code=status.get("ec"),
            )

        self._notify("[SRP] M2 ricevuto: verifico il server…")
        usr.verify_session(resp["M2"])
        if not usr.authenticated():
            raise GsaError("Verifica server SRP fallita (M2 non valido)")
        self._notify("[SRP] Sessione verificata: chiave di sessione stabilita")

        if "spd" not in resp:
            raise GsaError("Login ok ma mancano i dati di servizio (spd)")

        self._notify(f"[GSA] Decifro spd ({len(resp['spd'])} byte, AES-256-CBC)…")
        spd_plain = decrypt_spd(usr.get_session_key(), resp["spd"])
        spd = plistlib.loads(spd_plain, fmt=plistlib.FMT_XML)
        self._notify(f"[GSA] spd decifrata: {len(spd)} campi (adsid={str(spd.get('adsid',''))[:8]}…)")

        return self._build_session(spd)

    # ------------------------------------------------- token Xcode (apptokens)
    def fetch_xcode_token(self, spd: dict) -> str:
        """Scambia la sessione GSA con uno Xcode token (X-Apple-GS-Token),
        necessario per developerservices2.apple.com.

        Formato wire Apple della risposta cifrata: b"XYZ" (AAD) | IV (16) |
        ciphertext | tag GCM (16). Il checksum della richiesta è
        HMAC-SHA256(sk, b"apptokens" + adsid + app).
        """
        import base64 as b64

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        app = "com.apple.gs.xcode.auth"
        self._notify(f"[GSA] Richiedo Xcode token ({app}) via apptokens…")
        sk = spd["sk"]
        if isinstance(sk, str):
            sk = b64.b64decode(sk)
        adsid = spd["adsid"]
        checksum = hmac.new(sk, b"apptokens" + adsid.encode() + app.encode(), hashlib.sha256).digest()
        params = {
            "u": adsid,
            "app": [app],
            "c": spd["c"],
            "t": spd["GsIdmsToken"],
            "checksum": checksum,
            "o": "apptokens",
        }
        for attempt in range(1, APPTOKENS_ATTEMPTS + 1):
            try:
                resp = self._gsa_request(params)
                break
            except GsaError as e:
                if e.http_status not in RETRYABLE_STATUS:
                    raise
                if attempt == APPTOKENS_ATTEMPTS:
                    raise GsaError(
                        f"Apple ha rifiutato la richiesta del token di firma (apptokens, HTTP "
                        f"{e.http_status}) per {attempt} volte di fila, anche con connessioni nuove",
                        http_status=e.http_status,
                    ) from e
                wait = 2 * attempt
                self._notify(f"[GSA] Riprovo apptokens tra {wait} s con una connessione nuova "
                             f"(tentativo {attempt + 1}/{APPTOKENS_ATTEMPTS})…")
                self._sleep(wait)
        et = resp.get("et")
        if not et:
            raise GsaError(f"Risposta apptokens senza 'et': {resp.get('Status')}")
        if len(et) < 35 or et[:3] != b"XYZ":
            raise GsaError("Blob token cifrato malformato")
        self._notify(f"[GSA] Decifro token (AES-GCM, {len(et)} byte)…")
        iv = et[3:19]
        plaintext = AESGCM(sk).decrypt(iv, et[19:], b"XYZ")
        token_plist = plistlib.loads(plaintext)
        token_info = token_plist.get("t", {}).get(app)
        if not token_info or not token_info.get("token"):
            raise GsaError("Xcode token non presente nella risposta apptokens")
        self._notify("[GSA] Xcode token ottenuto")
        return str(token_info["token"])

    def _build_session(self, spd: dict) -> AppleSession:
        cookies = {
            k: v.value if hasattr(v, "value") else v
            for k, v in self._session.cookies.items()
        }
        self._notify(f"[GSA] Cookie ricevuti: {', '.join(sorted(cookies.keys())) or 'nessuno'}")
        tokens = {}
        t = spd.get("t") or {}
        for key, val in t.items():
            if isinstance(val, dict) and "token" in val:
                tokens[key] = val["token"]
        sk = spd.get("sk", b"")
        if isinstance(sk, str):
            sk = base64.b64decode(sk)
        session = AppleSession(
            cookies=cookies,
            dsid=str(spd.get("dsid", "")),
            adsid=str(spd.get("adsid", "")),
            tokens=tokens,
            account_info=spd,
            sk=sk,
        )
        # mint dello Xcode token per i servizi developer: senza non si firma nulla,
        # quindi l'errore (es. 503 su apptokens) arriva fino all'utente così com'è
        session.xcode_token = self.fetch_xcode_token(spd)
        logger.info("Xcode token ottenuto (scadenza: %s)", self._token_expiry(spd))
        if not session.logged_in:
            logger.warning("Cookie myacinfo assente nella risposta: la sessione potrebbe non funzionare per i servizi Apple")
        return session

    @staticmethod
    def _token_expiry(spd: dict) -> str:
        t = (spd.get("t") or {}).get("com.apple.gs.appleid.auth") or {}
        expiry = t.get("expiry")
        return f"{expiry}" if expiry else "?"


def default_anisette_getter():
    """Anisette getter comodo: iTunes se disponibile, altrimenti emulata."""
    from .anisette_provider import get_anisette_headers

    return get_anisette_headers()
