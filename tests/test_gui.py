"""Interfaccia: finestra principale e dialog di login, con backend finti."""
import base64
import os
import gc
import threading
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QDialog, QMessageBox

from fakes import FakeDevices, FakeFlow, make_ipa
from kanga.core import store
from kanga.core.device import DeviceInfo
from kanga.core.gsa import GsaError
from kanga.core.signer import ZsignError
from kanga.gui import main_window as mw
from kanga.gui.login_dialog import LoginDialog
from kanga.gui.workers import wait_all

UDID = "00008110-001A2B3C4D5E"


def trusted_device():
    return DeviceInfo(UDID, "iPhone di Mario", "18.1", "iPhone15,2", True, "ok")


@pytest.fixture
def make_window(qtbot, themed_app, tmp_path, monkeypatch):
    monkeypatch.setattr(mw.signer, "zsign_available", lambda *a: True)
    windows = []

    def factory(flow=None, devices=None, scan=True):
        flow = flow or FakeFlow()
        devices = devices if devices is not None else FakeDevices()
        settings = QSettings(str(tmp_path / f"settings{len(windows)}.ini"), QSettings.Format.IniFormat)
        w = mw.MainWindow(flow=flow, devices=devices, settings=settings, auto_refresh=False)
        qtbot.addWidget(w)
        windows.append(w)
        if scan:
            w.refresh_devices()
            qtbot.waitUntil(lambda: not w._scan_running, timeout=3000)
        return w

    yield factory
    for w in windows:
        w._busy = False  # evita la conferma di chiusura
    assert wait_all(5000)


@pytest.fixture
def ipa(tmp_path):
    return make_ipa(tmp_path / "Camera.ipa", name="Fotocamera Pro", bundle="com.example.camera", icon=True)


def test_initial_state_explains_what_is_missing(make_window):
    w = make_window(devices=FakeDevices([]))
    assert not w.btn_install.isEnabled()
    text = w.lbl_ready.text()
    assert "accedi con il tuo Apple ID" in text
    assert "collega un iPhone" in text
    assert "scegli un file .ipa" in text
    assert w.btn_account.text() == "Accedi"
    assert w.device_pill.text() == "Nessun dispositivo"


def test_ready_when_everything_is_set(make_window, ipa):
    w = make_window(FakeFlow(logged_in=True, name="mario@icloud.com"), FakeDevices([trusted_device()]))
    assert w.lbl_account.text() == "mario@icloud.com"
    assert w.cmb_devices.currentText() == "iPhone di Mario · iOS 18.1"
    assert w.device_pill.text() == "Pronto"
    assert not w.btn_install.isEnabled()
    assert w.load_ipa(ipa)
    assert w.drop.app_name.text() == "Fotocamera Pro"
    assert "Versione 1.2 (34)" in w.drop.app_meta.text()
    assert w.drop.app_icon.pixmap() is not None and not w.drop.app_icon.pixmap().isNull()
    assert w.btn_install.isEnabled()
    assert w.lbl_ready.text().startswith("Tutto pronto")
    w.clear_ipa()
    assert not w.btn_install.isEnabled()


def test_missing_zsign_blocks_install(make_window, ipa, monkeypatch):
    w = make_window(FakeFlow(logged_in=True), FakeDevices([trusted_device()]))
    w.load_ipa(ipa)
    monkeypatch.setattr(mw.signer, "zsign_available", lambda *a: False)
    w._update_tools_status()
    assert not w.btn_install.isEnabled()
    assert "zsign" in w.lbl_ready.text()
    assert w.btn_fetch_zsign.isVisibleTo(w)


def test_invalid_ipa_shows_error_banner(make_window, tmp_path):
    w = make_window()
    bad = tmp_path / "rotto.ipa"
    bad.write_text("non è uno zip")
    assert not w.load_ipa(bad)
    assert w.banner.isVisibleTo(w) and w.banner.kind == "error"
    assert w.banner.title.text() == "File IPA non valido"
    assert w._ipa_info is None


def test_drop_ipa_on_drop_zone(make_window, ipa):
    w = make_window()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(ipa))])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.drop.dropEvent(event)
    assert w._ipa_info is not None and w._ipa_info.bundle_id == "com.example.camera"


def test_untrusted_device_can_be_paired(make_window, qtbot):
    devices = FakeDevices([DeviceInfo(UDID, "", trusted=False, status="pending")])
    w = make_window(FakeFlow(logged_in=True), devices)
    assert w.btn_pair.isVisibleTo(w)
    assert "Autorizza" in w.device_pill.text()
    assert "autorizza questo computer" in w.lbl_ready.text()
    w.btn_pair.click()
    qtbot.waitUntil(lambda: devices.pair_calls == [UDID], timeout=3000)
    qtbot.waitUntil(lambda: w.device_pill.text() == "Pronto", timeout=3000)
    assert not w.btn_pair.isVisibleTo(w)


def test_pair_failure_shows_hint(make_window, qtbot):
    err = RuntimeError("Pairing fallito con X: NotPairedError")
    devices = FakeDevices([DeviceInfo(UDID, "", trusted=False, status="unpaired")], pair_error=err)
    w = make_window(FakeFlow(logged_in=True), devices)
    w.btn_pair.click()
    qtbot.waitUntil(lambda: w.device_pill.text() == "Non autorizzato", timeout=3000)
    assert "Autorizza" in w.lbl_device_help.text()
    assert w.btn_pair.isEnabled()


def test_usbmux_missing(make_window):
    w = make_window(devices=FakeDevices([], usbmux=False))
    assert w.device_pill.text() == "Servizio assente"
    assert "Apple Devices" in w.lbl_device_help.text()
    assert w.sb_usbmux.text() == "Apple Mobile Device assente"


def test_polling_detects_new_device(make_window, qtbot):
    devices = FakeDevices([])
    w = make_window(FakeFlow(logged_in=True), devices)
    assert w.cmb_devices.count() == 0
    devices.devices.append(trusted_device())
    w._poll_devices()
    qtbot.waitUntil(lambda: w.cmb_devices.count() == 1, timeout=3000)
    assert "Dispositivo collegato" in w.txt_log.toPlainText()
    devices.devices.clear()
    w._poll_devices()
    qtbot.waitUntil(lambda: w.cmb_devices.count() == 0, timeout=3000)
    assert "Dispositivo scollegato" in w.txt_log.toPlainText()


def test_install_success(make_window, ipa, qtbot):
    flow = FakeFlow(logged_in=True, name="mario@icloud.com")
    w = make_window(flow, FakeDevices([trusted_device()]))
    w.load_ipa(ipa)
    w.txt_name.setText("Camera")
    w.btn_install.click()
    assert w._busy and not w.btn_install.isEnabled() and not w.btn_account.isEnabled()
    qtbot.waitUntil(lambda: not w._busy, timeout=5000)

    call = flow.sideload_calls[0]
    assert call["udid"] == UDID and call["custom_name"] == "Camera"
    assert call["device_name"] == "iPhone di Mario"
    assert flow.sideload_thread is not threading.main_thread()  # la UI non si blocca

    assert w.banner.kind == "success"
    assert "«Camera» è installata su iPhone di Mario" in w.banner.title.text()
    assert "VPN e gestione dispositivo" in w.banner.message.text()
    assert "Modalità sviluppatore" in w.banner.message.text()  # iOS 18
    assert w.steps.state_of(len(mw.STEPS) - 1) == "done"
    assert w.progress.value() == w.progress.maximum()
    assert "passo sign" in w.txt_log.toPlainText()

    history = store.load_history()
    assert len(history) == 1
    entry = history[0]
    assert entry["app_name"] == "Camera" and entry["udid"] == UDID
    assert entry["ipa_path"] == str(ipa) and entry["expires"]
    assert base64.b64decode(entry["icon_png"]).startswith(b"\x89PNG")
    assert len(w.recent_items) == 1
    assert w.nav_buttons[mw.PAGE_RECENT].text() == "Recenti · 1"
    assert w.btn_install.isEnabled()


def test_install_failure_shows_friendly_error(make_window, ipa, qtbot):
    flow = FakeFlow(logged_in=True, sideload_error=ZsignError("zsign fallito (exit 1): bad"))
    w = make_window(flow, FakeDevices([trusted_device()]))
    w.load_ipa(ipa)
    w.start_install()
    qtbot.waitUntil(lambda: not w._busy, timeout=5000)
    assert w.banner.kind == "error"
    assert w.banner.title.text() == "Firma dell'app non riuscita"
    assert "decriptate" in w.banner.hint.text()
    sign_index = [k for k, _ in mw.STEPS].index("sign")
    assert w.steps.state_of(sign_index) == "failed"
    assert w.btn_install.isEnabled()
    assert store.load_history() == []
    assert "zsign fallito (exit 1): bad" in w.txt_log.toPlainText()


def test_reinstall_from_recent(make_window, ipa, qtbot):
    store.add_history_entry({
        "app_name": "Camera", "custom_name": "Camera", "bundle_id": "com.example.X", "udid": UDID,
        "device_name": "iPhone di Mario", "ipa_path": str(ipa),
        "expires": (datetime.now(timezone.utc) + timedelta(days=1, hours=2)).isoformat(),
    })
    flow = FakeFlow(logged_in=True)
    w = make_window(flow, FakeDevices([trusted_device()]))
    assert w.banner.kind == "warning" and w.banner.isVisibleTo(w)  # avviso di scadenza
    item = w.recent_items[0]
    assert item.expiry.text() == "Scade domani"
    assert item.btn_reinstall.isEnabled()
    w.show_page(mw.PAGE_RECENT)
    item.btn_reinstall.click()
    assert w.pages.currentIndex() == mw.PAGE_INSTALL
    qtbot.waitUntil(lambda: bool(flow.sideload_calls) and not w._busy, timeout=5000)
    assert flow.sideload_calls[0]["custom_name"] == "Camera"


def test_recent_with_missing_file_cannot_reinstall(make_window, tmp_path):
    store.add_history_entry({"app_name": "Persa", "bundle_id": "b", "udid": "U",
                             "ipa_path": str(tmp_path / "sparita.ipa")})
    w = make_window()
    item = w.recent_items[0]
    assert not item.btn_reinstall.isEnabled()
    assert "non trovato" in item.btn_reinstall.toolTip()
    del item
    # la riga elimina sé stessa dal proprio click: non deve essere distrutta durante il segnale
    w.recent_items[0].btn_remove.click()
    gc.collect()
    assert store.load_history() == [] and w.recent_items == []
    assert not w.recent_empty.isHidden()


def test_logout_asks_confirmation(make_window, monkeypatch):
    flow = FakeFlow(logged_in=True, name="mario@icloud.com")
    w = make_window(flow)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    w.btn_account.click()
    assert flow.logged_in and w.btn_account.text() == "Esci"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w.btn_account.click()
    assert not flow.logged_in
    assert w.btn_account.text() == "Accedi" and w.lbl_account.text() == "Non connesso"


def test_login_from_main_window_remembers_apple_id(make_window, monkeypatch):
    flow = FakeFlow()
    w = make_window(flow)

    class AcceptingDialog:
        apple_id = "mario@icloud.com"

        def exec(self):
            flow.logged_in, flow.name = True, self.apple_id
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(w, "_create_login_dialog", lambda: AcceptingDialog())
    w.btn_account.click()
    assert w.lbl_account.text() == "mario@icloud.com"
    assert w._settings.value("last_apple_id") == "mario@icloud.com"
    assert w.card_account.badge.text() == "✓"


def test_close_during_install_asks_confirmation(make_window, monkeypatch):
    w = make_window()
    w._busy = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    w.show()
    w.close()
    assert w.isVisible()
    w._busy = False
    w.close()
    assert not w.isVisible()


# ------------------------------------------------------------- login dialog
@pytest.fixture
def dialog(qtbot, themed_app):
    def factory(flow, apple_id=""):
        dlg = LoginDialog(flow, apple_id=apple_id)
        qtbot.addWidget(dlg)
        dlg.show()
        return dlg

    yield factory
    assert wait_all(5000)


def test_dialog_submit_enabled_only_with_credentials(dialog, qtbot):
    dlg = dialog(FakeFlow())
    assert not dlg.btn_submit.isEnabled()
    qtbot.keyClicks(dlg.txt_apple_id, "mario@icloud.com")
    assert not dlg.btn_submit.isEnabled()
    qtbot.keyClicks(dlg.txt_password, "segreta")
    assert dlg.btn_submit.isEnabled()
    dlg.act_reveal.trigger()
    assert dlg.txt_password.echoMode() == dlg.txt_password.EchoMode.Normal


def test_dialog_login_without_2fa(dialog, qtbot):
    flow = FakeFlow()
    dlg = dialog(flow, apple_id="mario@icloud.com")
    qtbot.keyClicks(dlg.txt_password, "segreta")
    qtbot.keyClick(dlg.txt_password, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: dlg.result() == QDialog.DialogCode.Accepted, timeout=3000)
    assert flow.login_calls == [("mario@icloud.com", "segreta")]
    assert dlg.session is not None


def test_dialog_2fa_wrong_then_right_code(dialog, qtbot):
    flow = FakeFlow(require_2fa=True)
    dlg = dialog(flow, apple_id="mario@icloud.com")
    qtbot.keyClicks(dlg.txt_password, "segreta")
    dlg.btn_submit.click()
    qtbot.waitUntil(dlg.on_code_page, timeout=3000)
    assert dlg.btn_submit.text() == "Verifica"
    qtbot.keyClicks(dlg.txt_code, "11 1a111")  # spazi e lettere ignorati; 6 cifre = invio automatico
    qtbot.waitUntil(lambda: dlg.err_code.isVisible(), timeout=3000)
    assert "Codice di verifica non valido" in dlg.err_code.text.text()
    assert dlg.txt_code.text() == "" and dlg.txt_code.isEnabled()
    qtbot.keyClicks(dlg.txt_code, "123456")
    qtbot.waitUntil(lambda: dlg.result() == QDialog.DialogCode.Accepted, timeout=3000)
    assert flow.gsa.submitted == ["111111", "123456"]


def test_dialog_resend_code(dialog, qtbot):
    flow = FakeFlow(require_2fa=True)
    dlg = dialog(flow, apple_id="a@b.c")
    qtbot.keyClicks(dlg.txt_password, "x")
    dlg.btn_submit.click()
    qtbot.waitUntil(dlg.on_code_page, timeout=3000)
    dlg.btn_resend.click()
    qtbot.waitUntil(lambda: flow.gsa.triggered == 2, timeout=3000)
    qtbot.waitUntil(lambda: dlg.txt_code.isEnabled(), timeout=3000)
    assert "Nuovo codice inviato" in dlg.lbl_status.text()
    dlg.reject()


def test_dialog_login_error_inline(dialog, qtbot):
    flow = FakeFlow(login_error=GsaError("Login fallito: credenziali non valide", apple_code=-22406))
    dlg = dialog(flow, apple_id="mario@icloud.com")
    qtbot.keyClicks(dlg.txt_password, "sbagliata")
    dlg.btn_submit.click()
    qtbot.waitUntil(lambda: dlg.err_credentials.isVisible(), timeout=3000)
    assert "Apple ID o password errati" in dlg.err_credentials.text.text()
    assert dlg.isVisible() and not dlg.on_code_page()
    assert dlg.txt_password.isEnabled() and dlg.btn_submit.isEnabled()


def test_dialog_cancel_during_2fa_stops_login(dialog, qtbot):
    flow = FakeFlow(require_2fa=True)
    dlg = dialog(flow, apple_id="a@b.c")
    qtbot.keyClicks(dlg.txt_password, "x")
    dlg.btn_submit.click()
    qtbot.waitUntil(dlg.on_code_page, timeout=3000)
    dlg.btn_cancel.click()
    assert dlg.result() == QDialog.DialogCode.Rejected
    assert wait_all(3000)
    assert not flow.logged_in


# ------------------------------------------------------ password salvata
windows_only = pytest.mark.skipif(os.name != "nt", reason="DPAPI solo su Windows")


@windows_only
def test_dialog_remember_saves_encrypted_password(dialog, qtbot):
    dlg = dialog(FakeFlow(), apple_id="mario@icloud.com")
    dlg.chk_remember.setChecked(True)
    qtbot.keyClicks(dlg.txt_password, "segreta")
    dlg.btn_submit.click()
    qtbot.waitUntil(lambda: dlg.result() == QDialog.DialogCode.Accepted, timeout=3000)
    assert store.load_credentials() == ("mario@icloud.com", "segreta")


@windows_only
def test_dialog_without_remember_forgets_password(dialog, qtbot):
    store.save_credentials("mario@icloud.com", "vecchia")
    dlg = dialog(FakeFlow(), apple_id="mario@icloud.com")
    assert not dlg.chk_remember.isChecked()
    qtbot.keyClicks(dlg.txt_password, "segreta")
    dlg.btn_submit.click()
    qtbot.waitUntil(lambda: dlg.result() == QDialog.DialogCode.Accepted, timeout=3000)
    assert store.load_credentials() is None


@windows_only
def test_install_with_saved_password_logs_in_automatically(make_window, ipa, qtbot):
    store.save_credentials("mario@icloud.com", "segreta")
    flow = FakeFlow()
    w = make_window(flow, FakeDevices([trusted_device()]))
    assert w.lbl_account.text() == "mario@icloud.com"
    assert "accedi" not in w.lbl_ready.text()
    assert w.load_ipa(ipa) and w.btn_install.isEnabled()
    w.btn_install.click()  # niente sessione: login automatico, poi l'installazione riparte
    qtbot.waitUntil(lambda: bool(flow.sideload_calls) and not w._busy, timeout=5000)
    assert flow.login_calls == [("mario@icloud.com", "segreta")]
    assert w.banner.kind == "success" and w.btn_account.text() == "Esci"
    assert store.load_credentials() == ("mario@icloud.com", "segreta")


@windows_only
def test_logout_also_forgets_saved_password(make_window, monkeypatch):
    store.save_credentials("mario@icloud.com", "segreta")
    flow = FakeFlow(logged_in=True, name="mario@icloud.com")
    w = make_window(flow)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w.btn_account.click()
    assert not flow.logged_in and not store.has_credentials()
    assert w.lbl_account.text() == "Non connesso"


# ------------------------------------------------------------ jailbreak
def iphone7(version="15.1"):
    return DeviceInfo(UDID, "iPhone di Mario", version, "iPhone9,3", True, "ok")


def test_jailbreak_page_without_device(make_window):
    w = make_window(devices=FakeDevices([]))
    assert w.nav_buttons[mw.PAGE_JAILBREAK].text() == "Jailbreak"
    assert w.jailbreak.rows == []
    assert w.jailbreak.lbl_device.text() == "Nessun dispositivo"
    assert not w.jailbreak.lbl_tools_empty.isHidden()


def test_jailbreak_page_lists_tools_for_device(make_window):
    w = make_window(devices=FakeDevices([iphone7()]))
    rows = {r.compat.tool.id: r for r in w.jailbreak.rows}
    assert w.jailbreak.lbl_device.text() == "iPhone 7"
    assert "chip A10" in w.jailbreak.lbl_device_sub.text()
    assert rows["dopamine"].compat.compatible
    assert [b.text() for b in rows["dopamine"].buttons] == ["Scarica e installa"]
    assert not rows["nugget"].compat.compatible and rows["nugget"].buttons == []
    assert [b.text() for b in rows["palera1n"].buttons] == ["Guida"]
    assert w.jailbreak.rows[0].compat.compatible  # i compatibili in cima


def test_jailbreak_download_goes_through_install_pipeline(make_window, ipa, qtbot, monkeypatch):
    from kanga.gui import jailbreak_page as jp

    seen = []

    def fake_fetch(tool, progress_cb=None):
        seen.append(threading.current_thread())
        progress_cb(40, "x")
        progress_cb(100, "x")
        return ipa

    monkeypatch.setattr(jp.downloads, "fetch_tool_ipa", fake_fetch)
    flow = FakeFlow(logged_in=True, name="mario@icloud.com")
    w = make_window(flow, FakeDevices([iphone7()]))
    w.show_page(mw.PAGE_JAILBREAK)
    row = next(r for r in w.jailbreak.rows if r.compat.tool.id == "dopamine")
    row.buttons[0].click()
    qtbot.waitUntil(lambda: bool(flow.sideload_calls) and not w._busy, timeout=5000)
    assert seen[0] is not threading.main_thread()  # download fuori dal thread UI
    assert flow.sideload_calls[0]["ipa"] == ipa and flow.sideload_calls[0]["udid"] == UDID
    assert w.pages.currentIndex() == mw.PAGE_INSTALL
    assert w.banner.kind == "success"
    assert "[Jailbreak] Download completato" in w.txt_log.toPlainText()
    assert all(b.isEnabled() for r in w.jailbreak.rows for b in r.buttons)


def test_jailbreak_download_failure_shows_banner(make_window, qtbot, monkeypatch):
    from kanga.gui import jailbreak_page as jp

    def fail(tool, progress_cb=None):
        raise jp.downloads.DownloadError("Nessun .ipa nella release")

    monkeypatch.setattr(jp.downloads, "fetch_tool_ipa", fail)
    w = make_window(FakeFlow(logged_in=True), FakeDevices([iphone7()]))
    row = next(r for r in w.jailbreak.rows if r.compat.tool.id == "dopamine")
    row.buttons[0].click()
    qtbot.waitUntil(lambda: not w.jailbreak.banner.isHidden(), timeout=3000)
    assert "Download di Dopamine non riuscito" in w.jailbreak.banner.title.text()
    assert w.jailbreak.progress.isHidden()
    assert row.buttons[0].isEnabled()
