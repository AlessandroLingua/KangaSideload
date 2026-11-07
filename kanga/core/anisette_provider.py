"""Provider di header anisette per l'autenticazione Apple.

Due fonti, scelte automaticamente da `AutoAnisette`:

1. **iTunes** (`itunes_anisette`): le DLL Apple originali (AOSKit) di iTunes /
   iCloud per Windows. Apple le tratta come un client Windows legittimo, quindi
   è la fonte preferita quando le DLL sono installate.
2. **Emulata** (`AnisetteProvider`): il pacchetto 'anisette' (Anisette.py)
   provisiona una macchina virtuale Apple stand-alone (emula le librerie ADI
   con Unicorn CPU). Non richiede iTunes, ma Apple a volte la rifiuta
   (errori -80045/-80046, HTTP 503).

La variabile d'ambiente KANGA_ANISETTE (= "itunes" | "emulata" | "auto")
forza la scelta. Lo stato emulato viene persistito in
%LOCALAPPDATA%/Kanga/anisette/ per non re-provisionare ad ogni avvio.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .store import base_dir

logger = logging.getLogger(__name__)

SOURCE_ITUNES = "iTunes (DLL Apple AOSKit)"
SOURCE_EMULATED = "emulata (Anisette.py)"


class AnisetteError(RuntimeError):
    pass


def anisette_dir() -> Path:
    return base_dir() / "anisette"


class AnisetteProvider:
    """Gestisce una sessione anisette emulata persistente."""

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


class AutoAnisette:
    """Sceglie la fonte anisette: iTunes se le DLL Apple ci sono e funzionano,
    altrimenti quella emulata. La scelta resta fissa fino al login successivo:
    login e servizi sviluppatore devono usare la stessa identità.
    """

    def __init__(self, mode: str | None = None, itunes_factory=None, emulated_factory=None):
        self.mode = (mode or os.environ.get("KANGA_ANISETTE") or "auto").strip().lower()
        self._itunes_factory = itunes_factory or self._default_itunes
        self._emulated_factory = emulated_factory or AnisetteProvider
        self._impl = None
        self.source = ""
        self.fallback_reason = ""
        self._lock = threading.Lock()

    @staticmethod
    def _default_itunes():
        from .itunes_anisette import ItunesAnisette

        return ItunesAnisette()

    def _choose(self):
        """Ritorna (fonte, header del tentativo di prova o None)."""
        self.fallback_reason = ""
        if self.mode in ("emulata", "emulated", "anisette"):
            self.source = SOURCE_EMULATED
            return self._emulated_factory(), None
        try:
            impl = self._itunes_factory()
            headers = impl.get_headers()  # prova subito: se fallisce si ripiega sull'emulata
        except Exception as e:
            if self.mode == "itunes":
                raise AnisetteError(f"Anisette iTunes non disponibile: {e}") from e
            self.fallback_reason = str(e)
            logger.info("Anisette iTunes non disponibile (%s): uso quella emulata", e)
            self.source = SOURCE_EMULATED
            return self._emulated_factory(), None
        self.source = SOURCE_ITUNES
        return impl, headers

    def get_headers(self) -> dict[str, str]:
        with self._lock:
            if self._impl is None:
                self._impl, first = self._choose()
                logger.info("Fonte anisette: %s", self.source)
                if first is not None:
                    return dict(first)  # header appena generati dalla prova: niente doppia chiamata
            impl = self._impl
        return dict(impl.get_headers())

    def retry_itunes(self) -> None:
        """Se si sta usando l'emulata, alla prossima richiesta riprova le DLL di iTunes
        (es. iTunes/iCloud installati mentre l'app era aperta). Da chiamare solo
        all'inizio di un nuovo login, mai a metà di un'operazione."""
        with self._lock:
            if self._impl is not None and self.source == SOURCE_EMULATED and self.mode == "auto":
                self._impl = None
                self.source = ""


_default: AutoAnisette | None = None


def default_provider() -> AutoAnisette:
    global _default
    if _default is None:
        _default = AutoAnisette()
    return _default


def get_anisette_headers() -> dict[str, str]:
    """Header anisette dalla fonte predefinita (iTunes se disponibile, altrimenti emulata)."""
    return default_provider().get_headers()
