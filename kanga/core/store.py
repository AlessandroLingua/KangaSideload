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
