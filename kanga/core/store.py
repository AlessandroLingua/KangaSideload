"""Persistenza della sessione Apple, delle chiavi di firma e dello storico.

Salvato in %LOCALAPPDATA%/Kanga/. La password dell'Apple ID NON viene mai
scritta su disco: solo i token di sessione (short-lived), l'indirizzo Apple ID
(per mostrarlo nell'interfaccia) e la chiave privata del certificato di
sviluppo. Attenzione: il token di sessione consente le operazioni di firma
finché scade — è nella cartella del tuo profilo utente.
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
