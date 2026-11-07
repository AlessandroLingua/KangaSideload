"""futurerestore: controlli sugli SHSH, comando, esecuzione, dialog e flusso nella sezione Firmware."""
import plistlib
import sys
import threading
from pathlib import Path

import pytest

from kanga.core import firmware as fwm
from kanga.gui.futurerestore_dialog import FuturerestoreDialog
from test_firmware_page import UDID, FakeFirmware, device, fw, rows_by_version, window  # noqa: F401

ECID = 0xABC
NONCE = bytes(range(32))
INFO = fwm.DeviceFirmwareInfo(UDID, "iPhone9,3", "d101ap", "16.1", "20B82", ecid=ECID, has_baseband=True)


def blob(ecid=ECID, nonce=NONCE, generator="0x1111111111111111", path="x.shsh2"):
    return fwm.BlobInfo(Path(path), ecid=ecid, ap_nonce=nonce, generator=generator)


# ------------------------------------------------------------------ core
def test_check_blob_ok():
    c = fwm.check_blob(INFO, blob(), NONCE)
    assert c.problems == [] and c.warnings == [] and c.nonce_ok is True


def test_check_blob_other_device_is_blocking():
    c = fwm.check_blob(INFO, blob(ecid=0xDEF), NONCE)
    assert c.problems and "altro dispositivo" in c.problems[0] and "0xDEF" in c.problems[0]


def test_check_blob_nonce_mismatch_is_a_warning():
    c = fwm.check_blob(INFO, blob(), b"\x00" * 32)
    assert c.problems == [] and c.nonce_ok is False
    assert "pwned DFU" in c.warnings[0] and "0x1111111111111111" in c.warnings[0]
    assert fwm.check_blob(INFO, blob(), None).nonce_ok is None


def test_read_blob(tmp_path, monkeypatch):
    class FakeIM4M:
        def __init__(self, data):
            assert data == b"TICKET"
            self.ecid, self.apnonce, self.chip_id, self.board_id = ECID, NONCE, 0x8010, 0x0C

    import pyimg4

    monkeypatch.setattr(pyimg4, "IM4M", FakeIM4M)
    path = tmp_path / "b.shsh2"
    path.write_bytes(plistlib.dumps({"ApImg4Ticket": b"TICKET", "generator": "0x1111111111111111"}))
    b = fwm.read_blob(path)
    assert (b.ecid, b.ap_nonce, b.chip_id, b.generator) == (ECID, NONCE, 0x8010, "0x1111111111111111")
    bad = tmp_path / "bad.shsh2"
    bad.write_bytes(b"non un plist")
    with pytest.raises(fwm.FirmwareError, match="SHSH non valido"):
        fwm.read_blob(bad)


def test_futurerestore_args():
    args = fwm.futurerestore_args("fr.exe", Path("b.shsh2"), Path("x.ipsw"))
    assert args == ["fr.exe", "-t", "b.shsh2", "--latest-sep", "--latest-baseband", "x.ipsw"]
    args = fwm.futurerestore_args("fr.exe", Path("b.shsh2"), Path("x.ipsw"), has_baseband=False, pwndfu=True)
    assert args == ["fr.exe", "-t", "b.shsh2", "--latest-sep", "--no-baseband",
                    "--use-pwndfu", "--set-nonce", "x.ipsw"]


def test_find_and_set_futurerestore(tmp_path, appdata, monkeypatch):
    monkeypatch.delenv("KANGA_FUTURERESTORE", raising=False)
    monkeypatch.setattr(fwm.shutil, "which", lambda name: None)
    assert fwm.find_futurerestore() is None
    exe = tmp_path / "futurerestore.exe"
    exe.write_bytes(b"MZ")
    assert fwm.set_futurerestore(exe) == exe and fwm.find_futurerestore() == exe
    with pytest.raises(fwm.FirmwareError, match="non trovato"):
        fwm.set_futurerestore(tmp_path / "manca.exe")


def test_run_futurerestore_streams_output(tmp_path):
    script = tmp_path / "fake_fr.py"
    script.write_text("import sys\nprint('Version: 2.0.0')\nprint('Restoring...')\nsys.exit(int(sys.argv[1]))\n")
    lines = []
    fwm.run_futurerestore([sys.executable, str(script), "0"], on_line=lines.append)
    assert lines == ["Version: 2.0.0", "Restoring..."]
    with pytest.raises(fwm.FirmwareError, match="codice 3"):
        fwm.run_futurerestore([sys.executable, str(script), "3"])


# ------------------------------------------------------------------ GUI
class FrBackend(FakeFirmware):
    """FakeFirmware con le funzioni di futurerestore (controlli veri, esecuzione finta)."""

    check_blob = staticmethod(fwm.check_blob)
    futurerestore_args = staticmethod(fwm.futurerestore_args)

    def __init__(self, tmp_path, *a, **k):
        super().__init__(tmp_path, *a, **k)
        self.exe = tmp_path / "futurerestore.exe"
        self.blob_ecid = ECID
        self.nonce = NONCE
        self.runs = []
        self.run_thread = None
        self.exited = []
        self.fail_with = None

    def read_ap_nonce(self, udid):
        return self.nonce

    def find_futurerestore(self):
        return self.exe

    def futurerestore_version(self, exe):
        return "futurerestore Version: 2.0.0"

    def set_futurerestore(self, path):
        self.exe = Path(path)
        return self.exe

    def shsh_dir(self):
        return self.tmp

    def read_blob(self, path):
        return blob(ecid=self.blob_ecid, path=path)

    def run_futurerestore(self, args, on_line=None):
        self.run_thread = threading.current_thread()
        self.runs.append(args)
        on_line("[Restore] fase 1")
        if self.fail_with:
            raise self.fail_with

    def exit_recovery(self, exe):
        self.exited.append(exe)


def make_dialog(qtbot, tmp_path, backend, nonce=NONCE):
    backend.shsh_saved["19A346"] = tmp_path / "old.shsh2"
    old = fw("15.0", "19A346", False)
    dlg = FuturerestoreDialog(backend, old, INFO, tmp_path / "old.ipsw", nonce)
    qtbot.addWidget(dlg)
    return dlg


def test_dialog_requires_backup_confirmation(qtbot, themed_app, tmp_path):
    dlg = make_dialog(qtbot, tmp_path, FrBackend(tmp_path, []))
    assert dlg.problems() == [] and "Version: 2.0.0" in dlg.lbl_exe.text()
    assert "✓ ECID 0xABC" in dlg.lbl_blob.text() and "nonce coincide" in dlg.lbl_blob.text()
    assert not dlg.btn_start.isEnabled()
    dlg.chk_backup.setChecked(True)
    assert dlg.btn_start.isEnabled()
    assert dlg.args() == [str(dlg.exe), "-t", str(tmp_path / "old.shsh2"), "--latest-sep",
                          "--latest-baseband", str(tmp_path / "old.ipsw")]


def test_dialog_blocks_blobs_of_another_device(qtbot, themed_app, tmp_path):
    backend = FrBackend(tmp_path, [])
    backend.blob_ecid = 0xDEF
    dlg = make_dialog(qtbot, tmp_path, backend)
    dlg.chk_backup.setChecked(True)
    assert not dlg.btn_start.isEnabled() and "altro dispositivo" in dlg.lbl_issues.text()


def test_dialog_nonce_mismatch_and_pwndfu(qtbot, themed_app, tmp_path):
    dlg = make_dialog(qtbot, tmp_path, FrBackend(tmp_path, []), nonce=b"\x00" * 32)
    dlg.chk_backup.setChecked(True)
    assert dlg.btn_start.isEnabled()  # avviso, non blocco
    assert "nonce diverso" in dlg.lbl_blob.text() and "pwned DFU" in dlg.lbl_issues.text()
    dlg.chk_pwndfu.setChecked(True)
    assert "--use-pwndfu" in dlg.args() and "--set-nonce" in dlg.args()
    assert "pwned DFU" not in dlg.lbl_issues.text()


def test_dialog_without_futurerestore_or_blob(qtbot, themed_app, tmp_path):
    backend = FrBackend(tmp_path, [])
    backend.exe = None
    dlg = FuturerestoreDialog(backend, fw("15.0", "19A346", False), INFO, tmp_path / "old.ipsw", NONCE)
    qtbot.addWidget(dlg)
    dlg.chk_backup.setChecked(True)
    assert not dlg.btn_start.isEnabled()
    assert "Scegli futurerestore" in dlg.lbl_issues.text() and "Nessun SHSH salvato" in dlg.lbl_issues.text()
    dlg.set_exe(tmp_path / "fr.exe")
    dlg.set_blob(tmp_path / "scelto.shsh2")
    assert dlg.btn_start.isEnabled() and "scelto.shsh2" in dlg.lbl_blob.text()


def unsigned_setup(window, tmp_path):
    backend = FrBackend(tmp_path, [fw("16.1.1", "20B101", True), fw("15.0", "19A346", False)], version="16.1")
    backend.local["19A346"] = tmp_path / "15.0.ipsw"
    w = window(backend, device("16.1"))
    return backend, w


def test_page_downgrade_with_futurerestore(window, tmp_path, qtbot, monkeypatch):
    backend, w = unsigned_setup(window, tmp_path)
    page = w.firmware
    row = rows_by_version(w)["15.0"]
    assert [b.text() for b in row.buttons] == ["Mostra file", "Ripristina con SHSH…"]
    assert row.buttons[1].isEnabled()
    seen = []

    def fake_dialog(f, path, nonce):
        seen.append((f.buildid, path, nonce))
        return ["fr.exe", "-t", "b.shsh2", "--latest-sep", "--latest-baseband", str(path)]

    monkeypatch.setattr(page, "open_futurerestore_dialog", fake_dialog)
    row.buttons[1].click()
    qtbot.waitUntil(lambda: bool(backend.runs) and not w._busy, timeout=3000)
    assert seen == [("19A346", backend.local["19A346"], NONCE)]
    assert backend.run_thread is not threading.main_thread()
    log = w.txt_log.toPlainText()
    assert "con futurerestore" in log and "[Restore] fase 1" in log
    assert page.banner.kind == "success"


def test_page_failed_downgrade_offers_exit_recovery(window, tmp_path, qtbot, monkeypatch):
    backend, w = unsigned_setup(window, tmp_path)
    backend.fail_with = fwm.FirmwareError("futurerestore terminato con errore (codice 255): SEP incompatibile")
    page = w.firmware
    monkeypatch.setattr(page, "open_futurerestore_dialog", lambda f, p, n: ["fr.exe", str(p)])
    rows_by_version(w)["15.0"].buttons[1].click()
    qtbot.waitUntil(lambda: page.banner.kind == "error" and not w._busy, timeout=3000)
    assert "SEP incompatibile" in page.banner.message.text()
    exit_btn = next(b for b in page.banner.findChildren(type(page.btn_cancel)) if b.text() == "Esci da Recovery")
    exit_btn.click()
    qtbot.waitUntil(lambda: backend.exited == ["fr.exe"], timeout=3000)


def test_page_cancelled_dialog_does_nothing(window, tmp_path, qtbot, monkeypatch):
    backend, w = unsigned_setup(window, tmp_path)
    page = w.firmware
    calls = []
    monkeypatch.setattr(page, "open_futurerestore_dialog", lambda *a: calls.append(a) or None)
    rows_by_version(w)["15.0"].buttons[1].click()
    qtbot.waitUntil(lambda: bool(calls), timeout=3000)
    assert backend.runs == [] and not w._busy


def test_unsigned_without_download_or_device_has_no_shsh_restore(window, tmp_path):
    backend = FrBackend(tmp_path, [fw("16.1.1", "20B101", True), fw("15.0", "19A346", False)], version="16.1")
    w = window(backend, device("16.1"))
    assert [b.text() for b in rows_by_version(w)["15.0"].buttons] == ["Scarica IPSW"]
