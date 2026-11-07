"""Worker QThread per eseguire operazioni lunghe senza bloccare la UI.

Regola d'oro: i widget Qt si toccano solo dal thread principale. Per questo
i callback dei worker sono collegati a *metodi* del QThread (che vive nel
thread principale): Qt li consegna in coda, nel thread giusto, anche quando
il segnale viene emesso dal thread di lavoro.
"""
from __future__ import annotations

import logging
import queue
import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..core.gsa import GsaError

logger = logging.getLogger(__name__)

# riferimenti ai thread attivi: un QThread distrutto mentre gira fa crashare Qt
_ACTIVE: set[QThread] = set()


def _track(thread: QThread) -> None:
    _ACTIVE.add(thread)
    thread.finished.connect(lambda: _ACTIVE.discard(thread))


def any_running() -> bool:
    return any(t.isRunning() for t in list(_ACTIVE))


def wait_all(timeout_ms: int = 3000) -> bool:
    """Attende la fine dei worker (usato in chiusura e nei test)."""
    ok = True
    for t in list(_ACTIVE):
        ok = t.wait(timeout_ms) and ok
    return ok


class GenericWorker(QThread):
    """Esegue una funzione in un thread separato."""

    finished_ok = Signal(object)  # risultato
    failed = Signal(object)       # eccezione

    def __init__(self, fn, *args, on_ok=None, on_fail=None, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._on_ok = on_ok
        self._on_fail = on_fail
        self.finished_ok.connect(self._deliver_ok)
        self.failed.connect(self._deliver_fail)
        _track(self)

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:
            logger.debug("worker fallito", exc_info=True)
            self.failed.emit(e)
        else:
            self.finished_ok.emit(result)

    @Slot(object)
    def _deliver_ok(self, result):
        if self._on_ok:
            self._on_ok(result)

    @Slot(object)
    def _deliver_fail(self, exc):
        if self._on_fail:
            self._on_fail(exc)


def start_worker(fn, *args, on_ok=None, on_fail=None, **kwargs) -> GenericWorker:
    w = GenericWorker(fn, *args, on_ok=on_ok, on_fail=on_fail, **kwargs)
    w.start()
    return w


class FlowBridge(QObject):
    """Inoltra gli eventi di AppFlow (emessi dai worker) al thread principale."""

    event = Signal(str, object)

    def __init__(self, handler, parent=None):
        super().__init__(parent)
        self._handler = handler
        self.event.connect(self._dispatch)

    def emit(self, tag: str, data) -> None:
        self.event.emit(tag, data)

    @Slot(str, object)
    def _dispatch(self, tag, data):
        self._handler(tag, data)


class LoginCancelled(Exception):
    pass


class LoginWorker(QThread):
    """Login Apple con 2FA interattivo.

    Quando Apple chiede il codice il worker emette `need_2fa` e resta in attesa
    di un'azione della GUI: `submit_code`, `resend_code` oppure `cancel`.
    Un codice errato non fa fallire il login: emette `code_rejected` e attende
    un nuovo codice.
    """

    status = Signal(str)
    need_2fa = Signal()
    code_rejected = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    cancelled = Signal()

    def __init__(self, flow, apple_id: str, password: str):
        super().__init__()
        self._flow = flow
        self._apple_id = apple_id
        self._password = password
        self._actions: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        _track(self)

    # -------------------------------------------------- azioni dalla GUI
    def submit_code(self, code: str) -> None:
        self._actions.put(("code", code))

    def resend_code(self) -> None:
        self._actions.put(("resend", None))

    def cancel(self) -> None:
        self._cancel.set()
        self._actions.put(("cancel", None))

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------------------------------------------------------- thread
    def run(self):
        try:
            session = self._flow.login(self._apple_id, self._password, on_2fa=self._on_2fa)
        except LoginCancelled:
            self.cancelled.emit()
            return
        except Exception as e:
            logger.debug("login fallito", exc_info=True)
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(e)
            return
        finally:
            self._password = ""  # non tenere la password in memoria più del necessario
        if self._cancel.is_set():
            # l'utente ha annullato mentre la richiesta era in volo: non lasciare
            # una sessione salvata che l'interfaccia non mostra
            try:
                self._flow.logout()
            except Exception:
                logger.debug("logout dopo annullamento fallito", exc_info=True)
            self.cancelled.emit()
        else:
            self.succeeded.emit(session)

    def _on_2fa(self, client):
        if self._cancel.is_set():
            raise LoginCancelled()
        self.status.emit("Invio del codice ai tuoi dispositivi Apple…")
        client.trigger_2fa()
        self.need_2fa.emit()
        while True:
            action, value = self._actions.get()
            if action == "cancel" or self._cancel.is_set():
                raise LoginCancelled()
            if action == "resend":
                self.status.emit("Invio di un nuovo codice…")
                try:
                    client.trigger_2fa()
                except Exception as e:
                    self.code_rejected.emit(f"Invio del codice non riuscito: {e}")
                else:
                    self.status.emit("Nuovo codice inviato ai tuoi dispositivi.")
                continue
            self.status.emit("Verifica del codice…")
            try:
                client.submit_2fa_code(value)
                return
            except GsaError as e:
                self.code_rejected.emit(str(e))
