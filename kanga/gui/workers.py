"""Worker QThread per eseguire operazioni lunghe senza bloccare la UI."""
from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QThread, Signal


class GenericWorker(QThread):
    """Esegue una funzione in un thread separato con eventi verso la UI."""

    event = Signal(str, str)      # tag, message
    finished_ok = Signal(object)  # risultato
    failed = Signal(str)          # messaggio errore

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished_ok.emit(result)
        except Exception as e:
            logging.getLogger(__name__).exception("worker fallito")
            self.failed.emit(str(e) or e.__class__.__name__)


class LoginFlow(QObject):
    """Login Apple con supporto 2FA: emette 'need_2fa' quando serve il codice."""

    need_2fa = Signal()
    finished_ok = Signal(object)
    failed = Signal(str)
    log = Signal(str)

    TWOFA_TIMEOUT_S = 180

    def __init__(self, flow, apple_id: str, password: str):
        super().__init__()
        self._flow = flow
        self._apple_id = apple_id
        self._password = password
        self._code = None
        self._code_available = None

    def provide_code(self, code: str):
        self._code = code
        if self._code_available:
            self._code_available()

    def run(self):
        try:
            def on_2fa(client):
                self.log.emit("Apple richiede il codice 2FA: lo invio ai tuoi dispositivi fidati...")
                client.trigger_2fa()
                self.need_2fa.emit()
                # attende che la GUI fornisca il codice (max 3 minuti)
                import threading

                evt = threading.Event()
                self._code_available = evt.set
                waited = 0.0
                while self._code is None and waited < self.TWOFA_TIMEOUT_S:
                    if evt.wait(timeout=0.2):
                        break
                    waited += 0.2
                if self._code is None:
                    raise TimeoutError(
                        "Nessun codice 2FA inserito entro 3 minuti: riprova il login"
                    )
                code = self._code
                self._code = None
                self.log.emit(f"Validazione codice 2FA ({code})…")
                client.submit_2fa_code(code)
                self.log.emit("Codice 2FA accettato")

            self.log.emit(f"[LOGIN] Avvio login per {self._apple_id}")
            session = self._flow.login(self._apple_id, self._password, on_2fa=on_2fa)
            self.log.emit("[LOGIN] Completato: sessione salvata")
            self.finished_ok.emit(session)
        except Exception as e:
            logging.getLogger(__name__).exception("login fallito")
            self.failed.emit(f"{e.__class__.__name__}: {e}" if not str(e) else str(e))


def start_worker(fn, on_ok=None, on_fail=None, on_event=None, *args, **kwargs) -> GenericWorker:
    w = GenericWorker(fn, *args, **kwargs)
    if on_ok:
        w.finished_ok.connect(on_ok)
    if on_fail:
        w.failed.connect(on_fail)
    if on_event:
        w.event.connect(on_event)
    w.start()
    return w
