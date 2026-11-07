"""Login: le credenziali restano disponibili per riprovare dopo un errore.

Il salvataggio DPAPI (solo Windows) è simulato in memoria, così i test girano
ovunque. Il dialog vero viene pilotato al posto di exec() da uno "script"
di azioni, come farebbe l'utente.
"""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog

from fakes import FakeDevices, FakeFlow
from kanga.core import store
from kanga.core.gsa import GsaError
from kanga.gui import main_window as mw
from kanga.gui.login_dialog import LoginDialog
from kanga.gui.workers import wait_all

APPLE_ID = "mario@icloud.com"
PASSWORD = "Segreta-123"
ACCEPTED, REJECTED = QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected


class RetryFlow(FakeFlow):
    """FakeFlow i cui primi login falliscono con gli errori indicati."""

    def __init__(self, errors=(), **kwargs):
        super().__init__(**kwargs)
        self.errors = list(errors)

    def login(self, apple_id, password, on_2fa=None):
        if self.errors:
            self.login_calls.append((apple_id, password))
            raise self.errors.pop(0)
        return super().login(apple_id, password, on_2fa)


@pytest.fixture
def disk(monkeypatch):
    """Credenziali 'su disco' simulate (DPAPI esiste solo su Windows)."""
    saved: dict = {}
    monkeypatch.setattr(store, "credentials_supported", lambda: True)
    monkeypatch.setattr(store, "save_credentials", lambda a, p: saved.update(creds=(a, p)))
    monkeypatch.setattr(store, "load_credentials", lambda: saved.get("creds"))
    monkeypatch.setattr(store, "clear_credentials", lambda: saved.pop("creds", None))
    monkeypatch.setattr(store, "has_credentials", lambda: "creds" in saved)
    monkeypatch.setattr(store, "saved_apple_id", lambda: saved.get("creds", ("",))[0])
    return saved


@pytest.fixture
def window(qtbot, themed_app, tmp_path, monkeypatch, disk):
    monkeypatch.setattr(mw.signer, "zsign_available", lambda *a: True)
    windows = []

    def factory(flow):
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        w = mw.MainWindow(flow=flow, devices=FakeDevices([]), settings=settings, auto_refresh=False)
        qtbot.addWidget(w)
        windows.append(w)
        return w

    yield factory
    assert wait_all(5000)  # ogni tentativo chiude il proprio thread di login


class DialogScript:
    """Sostituisce LoginDialog.exec: ogni apertura registra lo stato ed esegue un'azione."""

    def __init__(self, monkeypatch, qtbot, actions):
        self.actions = list(actions)
        self.seen: list[dict] = []
        script = self

        def fake_exec(dlg):
            err = dlg.err_credentials
            script.seen.append({
                "apple_id": dlg.apple_id,
                "password": dlg.password,
                "remember": dlg.remember,
                "error": "" if err.isHidden() else err.text.text(),
            })
            action = script.actions.pop(0) if script.actions else cancel
            return action(dlg, qtbot)

        monkeypatch.setattr(LoginDialog, "exec", fake_exec)


def _finish(dlg, qtbot):
    """Attende l'esito del tentativo; se fallisce l'utente chiude il dialog."""
    qtbot.waitUntil(lambda: dlg.last_attempt is not None and dlg._worker is None and not dlg._busy,
                    timeout=5000)
    if dlg.result() == ACCEPTED:
        return ACCEPTED
    dlg.reject()
    return REJECTED


def submit(apple_id=APPLE_ID, password=PASSWORD, remember=True):
    def action(dlg, qtbot):
        dlg.txt_apple_id.setText(apple_id)
        dlg.txt_password.setText(password)
        dlg.chk_remember.setChecked(remember)
        dlg.btn_submit.click()
        return _finish(dlg, qtbot)
    return action


def press_login(dlg, qtbot):
    """Riprova senza toccare i campi (devono essere già compilati)."""
    dlg.btn_submit.click()
    return _finish(dlg, qtbot)


def wait_auto_login(dlg, qtbot):
    """Login automatico con le credenziali salvate: nessuna azione dell'utente."""
    return _finish(dlg, qtbot)


def cancel(dlg, qtbot):
    dlg.reject()
    return REJECTED


def test_failed_login_keeps_credentials_and_reopens_prefilled(window, disk, qtbot, monkeypatch):
    flow = RetryFlow([GsaError("HTTP 503 all'endpoint GsService2")])
    w = window(flow)
    script = DialogScript(monkeypatch, qtbot, [submit(), cancel])
    assert not w._login()
    # salvate SUBITO (prima dell'esito), non solo a login riuscito
    assert disk["creds"] == (APPLE_ID, PASSWORD)
    assert "Accesso non riuscito" in w.txt_log.toPlainText()
    # riaprendo il dialog è già compilato, con l'errore precedente in evidenza
    assert not w._login()
    retry = script.seen[1]
    assert (retry["apple_id"], retry["password"], retry["remember"]) == (APPLE_ID, PASSWORD, True)
    assert "Apple ha rifiutato l'accesso" in retry["error"]
    assert flow.login_calls == [(APPLE_ID, PASSWORD)]  # dopo un errore niente nuovo tentativo automatico


def test_retry_with_one_click_after_failure(window, disk, qtbot, monkeypatch):
    flow = RetryFlow([GsaError("HTTP 503")])
    w = window(flow)
    DialogScript(monkeypatch, qtbot, [submit(), press_login])
    assert not w._login()
    assert w._login()
    assert flow.login_calls == [(APPLE_ID, PASSWORD), (APPLE_ID, PASSWORD)]
    assert w.btn_account.text() == "Esci"
    assert w._last_login_error is None


def test_without_remember_kept_only_for_the_session(window, disk, qtbot, monkeypatch):
    flow = RetryFlow([GsaError("Login fallito")])
    w = window(flow)
    script = DialogScript(monkeypatch, qtbot, [submit(remember=False), cancel])
    assert not w._login()
    assert "creds" not in disk  # niente su disco
    assert not w._login()
    again = script.seen[1]
    assert (again["apple_id"], again["password"]) == (APPLE_ID, PASSWORD)
    assert again["remember"] is False  # la scelta viene rispettata


def test_auto_login_uses_saved_credentials(window, disk, qtbot, monkeypatch):
    disk["creds"] = (APPLE_ID, PASSWORD)
    flow = RetryFlow()
    w = window(flow)
    DialogScript(monkeypatch, qtbot, [wait_auto_login])
    assert w._login()
    assert flow.login_calls == [(APPLE_ID, PASSWORD)]


def test_saved_credentials_failure_waits_for_the_user(window, disk, qtbot, monkeypatch):
    disk["creds"] = (APPLE_ID, PASSWORD)
    flow = RetryFlow([GsaError("HTTP 503")])
    w = window(flow)
    script = DialogScript(monkeypatch, qtbot, [wait_auto_login, press_login])
    assert not w._login()       # tentativo automatico fallito
    assert w._login()           # riaperto compilato: basta premere «Accedi»
    assert script.seen[1]["password"] == PASSWORD and script.seen[1]["error"]
    assert flow.login_calls == [(APPLE_ID, PASSWORD), (APPLE_ID, PASSWORD)]


def test_logout_forgets_the_last_attempt(window, disk, qtbot, monkeypatch):
    flow = RetryFlow()
    w = window(flow)
    script = DialogScript(monkeypatch, qtbot, [submit(remember=False), cancel])
    assert w._login()
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: mw.QMessageBox.StandardButton.Yes)
    w.btn_account.click()  # Esci
    w._login()
    assert script.seen[1]["password"] == ""


def test_closing_2fa_without_code_cancels_immediately(qtbot, themed_app, disk):
    flow = FakeFlow(require_2fa=True)
    dlg = LoginDialog(flow, apple_id=APPLE_ID, password=PASSWORD)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.btn_submit.click()
    qtbot.waitUntil(dlg.on_code_page, timeout=3000)
    dlg.reject()                 # 2FA chiuso senza codice
    assert wait_all(1000)        # il thread finisce subito, nessuna attesa di minuti
    assert not flow.logged_in
