"""Sezione Firmware della finestra principale, con backend firmware finto."""
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from fakes import FakeDevices, FakeFlow
from kanga.core import firmware as fwm
from kanga.core.device import DeviceInfo
from kanga.gui import firmware_page as fp
from kanga.gui import main_window as mw
from kanga.gui.workers import wait_all

UDID = "00008110-001A2B3C4D5E"
ITUNES = Path("C:/Program Files/iTunes/iTunes.exe")


def fw(version, build, signed):
    return fwm.Firmware("iPhone9,3", version, build, f"https://apple.example/{version}.ipsw",
                        size=6_000_000_000, signed=signed)


class FakeFirmware:
    relation = staticmethod(fwm.relation)

    def __init__(self, tmp_path, firmwares, version="15.8.4", libusb=True):
        self.tmp = tmp_path
        self.firmwares = firmwares
        self.version = version
        self.libusb = libusb
        self.local: dict[str, Path] = {}
        self.restored = []
        self.shsh = []
        self.download_gate = None
        self.restore_thread = None
        self.shsh_saved: dict[str, Path] = {}
        self.listed = []

    def list_models(self):
        return [fwm.Model("iPhone9,3", "iPhone 7 (GSM)"), fwm.Model("iPad16,3", "iPad Pro (M4, 11-inch, WiFi)")]

    def local_shsh(self, f, ecid=0):
        return self.shsh_saved.get(f.buildid)

    def read_device_info(self, udid):
        return fwm.DeviceFirmwareInfo(udid, "iPhone9,3", "d101ap", self.version, "19H390", ecid=0xABC)

    def list_firmwares(self, product_type):
        self.listed.append(product_type)
        if product_type == "iPad16,3":
            return [fwm.Firmware("iPad16,3", "18.1", "22B83", "https://apple.example/ipad.ipsw", signed=True),
                    fwm.Firmware("iPad16,3", "17.5", "21F79", "https://apple.example/ipad-old.ipsw")]
        return list(self.firmwares)

    def local_ipsw(self, f):
        return self.local.get(f.buildid)

    def download_ipsw(self, f, progress_cb=None, should_stop=None):
        progress_cb(3_000_000_000, f.size)
        if self.download_gate is not None:
            while not should_stop():
                self.download_gate.wait(0.01)
            raise fwm.DownloadCancelled("annullato")
        path = self.tmp / f.filename
        path.write_bytes(b"ipsw")
        self.local[f.buildid] = path
        return path

    def save_shsh(self, info, f):
        self.shsh.append((info.ecid, f.buildid))
        return self.tmp / "shsh" / "x.shsh2"

    def libusb_available(self):
        return self.libusb

    def libusb_dir(self):
        return self.tmp / "libusb"

    def restore(self, udid, path, erase=True):
        self.restore_thread = threading.current_thread()
        self.restored.append((udid, Path(path)))

    def itunes_exe(self):
        return ITUNES


def device(version="15.8.4", connection="USB"):
    return DeviceInfo(UDID, "iPhone di Mario", version, "iPhone9,3", True, "ok", connection)


@pytest.fixture
def window(qtbot, themed_app, tmp_path, monkeypatch):
    monkeypatch.setattr(mw.signer, "zsign_available", lambda *a: True)
    revealed, launched = [], []
    monkeypatch.setattr(fp, "_reveal", revealed.append)
    monkeypatch.setattr(fp, "_launch", launched.append)

    def factory(backend, dev):
        settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
        w = mw.MainWindow(flow=FakeFlow(logged_in=True), devices=FakeDevices([dev]), settings=settings,
                          auto_refresh=False, firmware=backend)
        qtbot.addWidget(w)
        w.refresh_devices()
        qtbot.waitUntil(lambda: not w._scan_running, timeout=3000)
        w.show_page(mw.PAGE_FIRMWARE)
        qtbot.waitUntil(lambda: not w.firmware._loading and bool(w.firmware.rows), timeout=3000)
        w.revealed, w.launched = revealed, launched
        return w

    yield factory
    assert wait_all(5000)


def rows_by_version(w):
    return {r.fw.version: r for r in w.firmware.rows}


def test_only_newer_signed_version_means_no_downgrade(window, tmp_path):
    backend = FakeFirmware(tmp_path, [fw("15.8.8", "19H422", True), fw("15.8.4", "19H390", False),
                                      fw("15.8.3", "19H386", False)])
    w = window(backend, device())
    page = w.firmware
    assert w.nav_buttons[mw.PAGE_FIRMWARE].text() == "Firmware"
    assert page.summary.title.text() == "Nessun downgrade possibile in questo momento"
    assert "Apple firma solo iOS 15.8.8" in page.summary.message.text()
    rows = rows_by_version(w)
    assert rows["15.8.8"].relation == fwm.UPGRADE
    assert [b.text() for b in rows["15.8.8"].buttons] == ["Salva SHSH", "Scarica IPSW"]
    assert [b.text() for b in rows["15.8.3"].buttons] == ["Scarica IPSW"]  # non firmata: solo download
    assert "ECID 0xABC" in page.lbl_device_sub.text() and "(19H390)" in page.lbl_device_sub.text()


def test_unsigned_list_is_collapsed(window, tmp_path):
    old = [fw(f"14.{i}", f"18A{i}", False) for i in range(12)]
    w = window(FakeFirmware(tmp_path, [fw("15.8.8", "19H422", True)] + old), device())
    assert len(w.firmware.rows) == 1 + fp.UNSIGNED_SHOWN
    w.firmware.btn_all.click()
    assert len(w.firmware.rows) == 13


def test_downgrade_flow_with_direct_restore(window, tmp_path, qtbot, monkeypatch):
    backend = FakeFirmware(tmp_path, [fw("16.1.1", "20B101", True), fw("16.0.3", "20A392", True)], version="16.1")
    w = window(backend, device("16.1"))
    page = w.firmware
    assert page.summary.title.text() == "Downgrade possibile a iOS 16.0.3"
    row = rows_by_version(w)["16.0.3"]
    assert row.relation == fwm.DOWNGRADE
    row.buttons[1].click()  # Scarica IPSW
    qtbot.waitUntil(lambda: "20A392" in backend.local and page._downloading is None, timeout=3000)
    row = rows_by_version(w)["16.0.3"]
    assert row.buttons[1].text() == "Ripristina…"

    row.buttons[0].click()  # Salva SHSH
    qtbot.waitUntil(lambda: bool(backend.shsh), timeout=3000)
    assert backend.shsh == [(0xABC, "20A392")]
    row = rows_by_version(w)["16.0.3"]  # la lista si ricostruisce dopo il salvataggio

    asked = []
    monkeypatch.setattr(page, "ask_restore_method", lambda f, ok: asked.append(ok) or "direct")
    row.buttons[1].click()
    assert asked == [True] and w._busy and "Ripristino del firmware" in w.btn_install.text()
    qtbot.waitUntil(lambda: not w._busy, timeout=3000)
    assert backend.restored == [(UDID, backend.local["20A392"])]
    assert backend.restore_thread is not threading.main_thread()
    assert page.banner.kind == "success" and "Ripristino di iOS 16.0.3" in w.txt_log.toPlainText()


def test_restore_with_itunes_opens_itunes_and_file(window, tmp_path, monkeypatch):
    backend = FakeFirmware(tmp_path, [fw("16.0.3", "20A392", True)], version="16.1", libusb=False)
    backend.local["20A392"] = tmp_path / "16.0.3.ipsw"
    w = window(backend, device("16.1"))
    asked = []
    monkeypatch.setattr(w.firmware, "ask_restore_method", lambda f, ok: asked.append(ok) or "itunes")
    rows_by_version(w)["16.0.3"].buttons[1].click()
    assert asked == [False]  # senza libusb solo iTunes
    assert w.revealed == [backend.local["20A392"]] and w.launched == [ITUNES]
    assert "Maiusc" in w.firmware.banner.message.text()
    assert backend.restored == [] and not w._busy
    assert "libusb" in w.txt_log.toPlainText()


def test_wifi_device_cannot_restore(window, tmp_path):
    backend = FakeFirmware(tmp_path, [fw("16.0.3", "20A392", True)], version="16.1")
    backend.local["20A392"] = tmp_path / "16.0.3.ipsw"
    w = window(backend, device("16.1", connection="Network"))
    shsh, restore = rows_by_version(w)["16.0.3"].buttons
    assert not shsh.isEnabled() and not restore.isEnabled()
    assert "serve il cavo USB" in w.firmware.lbl_device_sub.text()


def test_download_can_be_cancelled(window, tmp_path, qtbot):
    backend = FakeFirmware(tmp_path, [fw("15.8.8", "19H422", True)])
    backend.download_gate = threading.Event()
    w = window(backend, device())
    page = w.firmware
    rows_by_version(w)["15.8.8"].buttons[1].click()
    qtbot.waitUntil(lambda: page.progress.value() == 500, timeout=3000)
    assert "di" in page.lbl_progress.text() and not page.progress_row.isHidden()
    page.btn_cancel.click()
    qtbot.waitUntil(lambda: page._downloading is None, timeout=3000)
    assert page.banner.title.text() == "Download in pausa" and page.progress_row.isHidden()


def test_any_model_can_be_chosen_without_device(window, tmp_path, qtbot):
    backend = FakeFirmware(tmp_path, [fw("15.8.8", "19H422", True)])
    w = window(backend, device())
    page = w.firmware
    qtbot.waitUntil(lambda: bool(page.models), timeout=3000)
    assert page.cmb_model.itemText(0) == "Dispositivo collegato (iPhone 7 (GSM))"
    assert page.select_model("iPad16,3")
    qtbot.waitUntil(lambda: {r.fw.identifier for r in page.rows} == {"iPad16,3"}, timeout=3000)
    assert backend.listed[-1] == "iPad16,3" and not page.device_matches()
    assert page.lbl_device.text() == "iPad Pro (M4, 11-inch, WiFi)"
    assert "nessun dispositivo di questo modello collegato" in page.lbl_device_sub.text()
    assert page.summary.title.text().startswith("iPad Pro (M4, 11-inch, WiFi): 2 versioni")
    for r in page.rows:  # senza il dispositivo: solo download, anche per le firmate
        assert [b.text() for b in r.buttons] == ["Scarica IPSW"] and r.relation is None
    page.select_model("")  # di nuovo il dispositivo collegato
    qtbot.waitUntil(lambda: {r.fw.identifier for r in page.rows} == {"iPhone9,3"}, timeout=3000)
    assert page.device_matches()


def test_unsigned_version_can_be_downloaded_but_not_installed(window, tmp_path, qtbot):
    backend = FakeFirmware(tmp_path, [fw("16.1.1", "20B101", True), fw("15.0", "19A346", False)], version="16.1")
    backend.shsh_saved["19A346"] = tmp_path / "old.shsh2"
    w = window(backend, device("16.1"))
    page = w.firmware
    row = rows_by_version(w)["15.0"]
    assert any(isinstance(c, fp.QLabel) and c.text() == "SHSH salvati" for c in row.findChildren(fp.QLabel))
    row.buttons[0].click()
    qtbot.waitUntil(lambda: "19A346" in backend.local and page._downloading is None, timeout=3000)
    assert "solo per archivio o per futurerestore" in page.banner.hint.text()
    row = rows_by_version(w)["15.0"]
    assert [b.text() for b in row.buttons] == ["Mostra file", "Ripristina con SHSH…"]
    row.buttons[0].click()
    assert w.revealed == [backend.local["19A346"]]
