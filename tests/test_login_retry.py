"""Login: le credenziali restano disponibili per riprovare dopo un errore.

Gira con Qt offscreen; il salvataggio DPAPI (solo Windows) è simulato in memoria.
Uso: python -m pytest tests/test_login_retry.py
"""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog  # noqa: E402

from kanga.core import store  # noqa: E402
from kanga.core.gsa import AppleSession, GsaError  # noqa: E402
from kanga.gui import main_window as mw  # noqa: E402
from kanga.gui.login_dialog import LoginDialog  # noqa: E402

APPLE_ID = "mario@icloud.com"
PASSWORD = "Segreta-123"


@pytest.fixture
def disk(monkeypatch, tmp_path):
    """Credenziali 'su disco' simulate (DPAPI esiste solo su Windows)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    saved: dict = {}
    monkeypatch.setattr(store, "save_credentials", lambda a, p: saved.update(creds=(a, p)))
    monkeypatch.setattr(store, "load_credentials", lambda: saved.get("creds"))
    monkeypatch.setattr(store, "clear_credentials", lambda: saved.pop("creds", None))
    return saved


class DialogScript:
    """Sostituisce LoginDialog.exec: ogni apertura esegue la prossima azione."""

    def __init__(self, monkeypatch, actions):
        self.actions = list(actions)
        self.seen: list[dict] = []  # stato del dialog a ogni apertura
        script = self

        def fake_exec(dlg):
            script.seen.append({
                "apple_id": dlg.apple_id,
                "password": dlg.password,
                "remember": dlg.remember,
                "status": dlg.lbl_status.text(),
                "twofa": not dlg.frame_2fa.isHidden(),
            })
            action = script.actions.pop(0) if script.actions else "cancel"
            return action(dlg) if callable(action) else QDialog.DialogCode.Rejected

        monkeypatch.setattr(LoginDialog, "exec", fake_exec)


def type_credentials(apple_id=APPLE_ID, password=PASSWORD, remember=True):
    def action(dlg):
        dlg.txt_apple_id.setText(apple_id)
        dlg.txt_password.setText(password)
        dlg.chk_remember.setChecked(remember)
        return QDialog.DialogCode.Accepted
    return action


def press_login(dlg):
    """Riprova senza toccare i campi (devono essere già compilati)."""
    return QDialog.DialogCode.Accepted


@pytest.fixture
def window(qtbot, disk, monkeypatch):
    w = mw.MainWindow()
    qtbot.addWidget(w)
    attempts: list[tuple[str, str]] = []
    w.login_attempts = attempts
    w.login_errors = []  # eccezioni da sollevare ai prossimi tentativi

    def fake_login(apple_id, password, on_2fa=None):
        attempts.append((apple_id, password))
        if w.login_errors:
            err = w.login_errors.pop(0)
            if err == "2fa":
                on_2fa(type("Client", (), {"trigger_2fa": lambda self: None,
                                           "submit_2fa_code": lambda self, c: None})())
                return AppleSession(xcode_token="XT")
            raise err
        return AppleSession(xcode_token="XT")

    monkeypatch.setattr(w.flow, "login", fake_login)
    yield w
    t = getattr(w, "_login_thread", None)
    if t is not None:
        t.quit()
        t.wait(3000)


def _wait_attempts(qtbot, w, n):
    qtbot.waitUntil(lambda: len(w.login_attempts) >= n, timeout=5000)


def test_failed_login_keeps_credentials_and_reopens_prefilled(window, disk, qtbot, monkeypatch):
    w = window
    w.login_errors = [GsaError("HTTP 503 all'endpoint GsService2")]
    script = DialogScript(monkeypatch, [type_credentials(), "cancel"])
    w._open_login()
    _wait_attempts(qtbot, w, 1)
    # salvate SUBITO (prima dell'esito), non solo a login riuscito
    assert disk["creds"] == (APPLE_ID, PASSWORD)
    # dopo l'errore il dialog si riapre da solo, già compilato e con l'errore
    qtbot.waitUntil(lambda: len(script.seen) == 2, timeout=5000)
    retry = script.seen[1]
    assert (retry["apple_id"], retry["password"]) == (APPLE_ID, PASSWORD)
    assert retry["remember"] is True
    assert "503" in retry["status"]
    # il thread di login è stato chiuso
    qtbot.waitUntil(lambda: w._login_thread.isFinished(), timeout=3000)


def test_retry_with_one_click_after_failure(window, disk, qtbot, monkeypatch):
    w = window
    w.login_errors = [GsaError("HTTP 503")]
    DialogScript(monkeypatch, [type_credentials(), press_login])
    w._open_login()
    _wait_attempts(qtbot, w, 2)
    assert w.login_attempts == [(APPLE_ID, PASSWORD), (APPLE_ID, PASSWORD)]
    qtbot.waitUntil(lambda: w.btn_login.text() == "Esci / cambia account", timeout=3000)


def test_without_remember_kept_only_for_the_session(window, disk, qtbot, monkeypatch):
    w = window
    w.login_errors = [GsaError("Login fallito")]
    script = DialogScript(monkeypatch, [type_credentials(remember=False), "cancel"])
    w._open_login()
    _wait_attempts(qtbot, w, 1)
    assert "creds" not in disk  # niente su disco
    qtbot.waitUntil(lambda: len(script.seen) == 2, timeout=5000)
    assert (script.seen[1]["apple_id"], script.seen[1]["password"]) == (APPLE_ID, PASSWORD)
    assert script.seen[1]["remember"] is False  # la scelta viene rispettata


def test_reopening_manually_is_prefilled_too(window, disk, qtbot, monkeypatch):
    w = window
    w.login_errors = [GsaError("Login fallito")]
    script = DialogScript(monkeypatch, [type_credentials(remember=False), "cancel", "cancel"])
    w._open_login()
    qtbot.waitUntil(lambda: len(script.seen) == 2, timeout=5000)
    w._open_login()  # l'utente preme di nuovo «Accedi con Apple ID»
    assert (script.seen[2]["apple_id"], script.seen[2]["password"]) == (APPLE_ID, PASSWORD)


def test_auto_login_uses_saved_credentials(window, disk, qtbot, monkeypatch):
    disk["creds"] = (APPLE_ID, PASSWORD)
    w = window
    DialogScript(monkeypatch, [])
    w._open_login(auto=True)
    _wait_attempts(qtbot, w, 1)
    # prima partiva con Apple ID e password vuoti
    assert w.login_attempts == [(APPLE_ID, PASSWORD)]


def test_closing_2fa_without_code_cancels_immediately(window, disk, qtbot, monkeypatch):
    w = window
    w.login_errors = ["2fa"]
    script = DialogScript(monkeypatch, [type_credentials(), "cancel"])  # 2FA chiuso senza codice
    w._open_login()
    qtbot.waitUntil(lambda: w._login_thread.isFinished(), timeout=3000)  # niente attesa di 3 minuti
    assert script.seen[1]["twofa"] is True
    qtbot.wait(100)
    assert len(script.seen) == 2  # annullato: il dialog non si riapre
    assert "[LOGIN] Annullato" in w.txt_log.toPlainText()
