"""Persistenza della sessione Apple e delle chiavi di firma.

Salvato in %LOCALAPPDATA%/Kanga/. La password dell'Apple ID NON viene mai
scritta su disco: solo i token di sessione (short-lived) e il .p12 della
firma (protetto da password). Attenzione: il token di sessione consente le
operazioni di firma finché scade — è nella cartella del tuo profilo utente.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def base_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "Kanga"


def session_path() -> Path:
    return base_dir() / "session.kanga.json"


def keys_dir() -> Path:
    d = base_dir() / "keys"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session(data: dict) -> None:
    base_dir().mkdir(parents=True, exist_ok=True)
    session_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Sessione salvata in %s", session_path())


def load_session() -> dict | None:
    p = session_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Sessione illeggibile (%s): la ignoro", e)
        return None


def clear_session() -> None:
    session_path().unlink(missing_ok=True)


def session_from_gsa(apple_session) -> dict:
    """Serializza l'AppleSession del gsa per il salvataggio."""
    return {
        "cookies": apple_session.cookies,
        "dsid": apple_session.dsid,
        "adsid": apple_session.adsid,
        "xcode_token": apple_session.xcode_token,
        "tokens": apple_session.tokens,
        "sk": base64.b64encode(apple_session.sk).decode() if apple_session.sk else "",
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


# ---------------------------------------------------------------- credenziali
# Salvate cifrate con Windows DPAPI (scope utente): solo il tuo account Windows
# su questa macchina può decifrare. Mai in chiaro su disco.

_CRED_PATH = None


def credentials_path() -> Path:
    return base_dir() / "credentials.kanga.json"


def _dpapi_protect(data: bytes) -> bytes:
    import ctypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), "KangaSideload", None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("CryptProtectData fallita")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("CryptUnprotectData fallita")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def save_credentials(apple_id: str, password: str) -> None:
    base_dir().mkdir(parents=True, exist_ok=True)
    blob = _dpapi_protect(password.encode("utf-8"))
    credentials_path().write_text(
        json.dumps(
            {
                "apple_id": apple_id,
                "password_protected": base64.b64encode(blob).decode(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Credenziali salvate (cifrate DPAPI) per %s", apple_id)


def load_credentials() -> tuple[str, str] | None:
    """Ritorna (apple_id, password) o None. Richiede lo stesso utente Windows."""
    p = credentials_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        blob = base64.b64decode(data["password_protected"])
        password = _dpapi_unprotect(blob).decode("utf-8")
        return data["apple_id"], password
    except Exception as e:
        logger.warning("Credenziali non decifrabili (%s): le ignoro", e)
        return None


def clear_credentials() -> None:
    credentials_path().unlink(missing_ok=True)
