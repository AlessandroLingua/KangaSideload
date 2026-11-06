"""Persistenza della sessione Apple, delle chiavi di firma e dello storico.

Salvato in %LOCALAPPDATA%/Kanga/: i token di sessione (short-lived),
l'indirizzo Apple ID (per mostrarlo nell'interfaccia) e la chiave privata del
certificato di sviluppo. Attenzione: il token di sessione consente le
operazioni di firma finché scade — è nella cartella del tuo profilo utente.

La password dell'Apple ID viene salvata SOLO se l'utente lo chiede (casella
nel dialog di login) e sempre cifrata con Windows DPAPI: solo lo stesso utente
Windows sulla stessa macchina può decifrarla. Mai in chiaro su disco.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_HISTORY = 30


def base_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "Kanga"


def session_path() -> Path:
    return base_dir() / "session.kanga.json"


def history_path() -> Path:
    return base_dir() / "history.json"


def credentials_path() -> Path:
    return base_dir() / "credentials.kanga.json"


def downloads_dir() -> Path:
    """IPA scaricati dal tab Jailbreak (cache, si può svuotare liberamente)."""
    return base_dir() / "downloads"


def keys_dir() -> Path:
    d = base_dir() / "keys"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _restrict(path: Path) -> None:
    """Permessi 600 dove supportato (su Windows la cartella utente è già privata)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _restrict(path)


# ------------------------------------------------------------------ sessione
def save_session(data: dict) -> None:
    write_private(session_path(), json.dumps(data, indent=2).encode("utf-8"))
    logger.info("Sessione salvata in %s", session_path())


def load_session() -> dict | None:
    p = session_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Sessione illeggibile (%s): la ignoro", e)
        return None
    return data if isinstance(data, dict) else None


def clear_session() -> None:
    session_path().unlink(missing_ok=True)


def session_from_gsa(apple_session, apple_id: str = "") -> dict:
    """Serializza l'AppleSession del gsa per il salvataggio."""
    return {
        "apple_id": apple_id,
        "cookies": apple_session.cookies,
        "dsid": apple_session.dsid,
        "adsid": apple_session.adsid,
        "xcode_token": apple_session.xcode_token,
        "tokens": apple_session.tokens,
        "sk": base64.b64encode(apple_session.sk).decode() if apple_session.sk else "",
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def session_to_apple(data: dict):
    """Ricostruisce un AppleSession dal dizionario salvato."""
    from .gsa import AppleSession

    return AppleSession(
        cookies=data.get("cookies", {}),
        dsid=data.get("dsid", ""),
        adsid=data.get("adsid", ""),
        tokens=data.get("tokens", {}),
        xcode_token=data.get("xcode_token", ""),
        sk=base64.b64decode(data["sk"]) if data.get("sk") else b"",
    )


# -------------------------------------------------------------------- storico
def load_history() -> list[dict]:
    """Installazioni recenti, dalla più nuova alla più vecchia."""
    p = history_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Storico illeggibile (%s): lo ignoro", e)
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("bundle_id")]


def save_history(entries: list[dict]) -> None:
    base_dir().mkdir(parents=True, exist_ok=True)
    history_path().write_text(json.dumps(entries[:MAX_HISTORY], indent=2), encoding="utf-8")


def _same_install(a: dict, b: dict) -> bool:
    return a.get("bundle_id") == b.get("bundle_id") and a.get("udid") == b.get("udid")


def add_history_entry(entry: dict) -> list[dict]:
    """Aggiunge (o aggiorna) un'installazione: una sola voce per app+dispositivo."""
    entries = [e for e in load_history() if not _same_install(e, entry)]
    entries.insert(0, entry)
    save_history(entries)
    return entries[:MAX_HISTORY]


def remove_history_entry(bundle_id: str, udid: str) -> list[dict]:
    target = {"bundle_id": bundle_id, "udid": udid}
    entries = [e for e in load_history() if not _same_install(e, target)]
    save_history(entries)
    return entries


# ---------------------------------------------------------------- credenziali
def _dpapi(data: bytes, protect: bool) -> bytes:
    """Cifra/decifra con Windows DPAPI (scope utente corrente)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    if os.name != "nt":
        raise OSError("DPAPI disponibile solo su Windows")
    crypt32 = ctypes.windll.crypt32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), "KangaSideload", None, None, None, 0, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptProtectData fallita" if protect else "CryptUnprotectData fallita")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def credentials_supported() -> bool:
    return os.name == "nt"


def save_credentials(apple_id: str, password: str) -> None:
    blob = _dpapi(password.encode("utf-8"), protect=True)
    data = {"apple_id": apple_id, "password_protected": base64.b64encode(blob).decode()}
    write_private(credentials_path(), json.dumps(data, indent=2).encode("utf-8"))
    logger.info("Credenziali salvate (cifrate DPAPI) per %s", apple_id)


def load_credentials() -> tuple[str, str] | None:
    """Ritorna (apple_id, password) o None. Funziona solo per lo stesso utente Windows."""
    p = credentials_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        password = _dpapi(base64.b64decode(data["password_protected"]), protect=False).decode("utf-8")
        return str(data["apple_id"]), password
    except Exception as e:
        logger.warning("Credenziali salvate non decifrabili (%s): le ignoro", e)
        return None


def has_credentials() -> bool:
    return credentials_path().exists()


def saved_apple_id() -> str:
    """Apple ID delle credenziali salvate, senza decifrare la password."""
    try:
        return str(json.loads(credentials_path().read_text(encoding="utf-8")).get("apple_id") or "")
    except Exception:
        return ""


def clear_credentials() -> None:
    credentials_path().unlink(missing_ok=True)
