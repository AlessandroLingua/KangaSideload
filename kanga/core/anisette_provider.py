"""Provider di header anisette per l'autenticazione Apple.

Usa il pacchetto 'anisette' (Anisette.py): provisiona una macchina virtuale
Apple stand-alone (emula le librerie ADI con Unicorn CPU) e genera gli
header X-Apple-I-MD / X-Apple-I-MD-M ecc. Non richiede iPhone né iTunes.

Lo stato (librerie + provisioning) viene persistito in
%LOCALAPPDATA%/Kanga/anisette/ per non re-provisionare ad ogni avvio.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class AnisetteError(RuntimeError):
    pass


def base_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "Kanga"


def anisette_dir() -> Path:
    return base_dir() / "anisette"


class AnisetteProvider:
    """Gestisce una sessione anisette persistente."""

    def __init__(self, name: str = "default", data_dir: Path | None = None):
        self._name = name
        self._dir = (data_dir or anisette_dir())
        self._dir.mkdir(parents=True, exist_ok=True)
        self._libs_path = self._dir / "libs.bin"
        self._prov_path = self._dir / f"{name}.prov"
        self._session = None

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        from anisette import Anisette

        if self._prov_path.exists() and self._libs_path.exists():
            logger.info("Carico sessione anisette '%s'", self._name)
            self._session = Anisette.load(self._libs_path, self._prov_path)
            return self._session
        logger.info("Creo nuova sessione anisette '%s' (il primo avvio scarica le librerie ADI)", self._name)
        # guarda: libs.bin vuoto o corrotto (es. prima esecuzione interrotta) -> reset
        if self._libs_path.exists() and self._libs_path.stat().st_size < 1024:
            logger.warning("libs.bin vuoto/corrotto: lo rigenero")
            self._libs_path.unlink()
        if self._libs_path.exists():
            self._session = Anisette.init(self._libs_path)
        else:
            self._session = Anisette.init()
            self._session.save_libs(self._libs_path)
        self.save_provisioning()
        return self._session

    def get_headers(self) -> dict[str, str]:
        """Header anisette pronti per le richieste Apple (auto-provision)."""
        try:
            session = self._ensure_session()
            return dict(session.get_data())
        except Exception as e:
            raise AnisetteError(
                f"Provisioning anisette fallito: {e}. "
                "Se l'errore persiste usa 'reset()' e riprova (errore tipico Apple: -80045)."
            ) from e

    def save_provisioning(self) -> None:
        if self._session is None:
            return
        self._session.save_provisioning(self._prov_path)

    def reset(self) -> None:
        """Cancella lo stato e riprovisiona da zero (rimedio per errori Apple)."""
        self._session = None
        self._prov_path.unlink(missing_ok=True)
        self._libs_path.unlink(missing_ok=True)
        logger.info("Stato anisette azzerato")

    def close(self) -> None:
        try:
            self.save_provisioning()
        except Exception:
            pass


def get_anisette_headers(name: str = "default") -> dict[str, str]:
    """Funzione comoda one-shot (crea/carica la sessione persistente)."""
    return AnisetteProvider(name).get_headers()
