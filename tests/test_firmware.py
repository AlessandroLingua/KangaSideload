"""Firmware: elenco versioni (ipsw.me), download IPSW con ripresa e verifica, SHSH, libusb."""
import hashlib

import pytest

from kanga.core import firmware as fwm
from kanga.core import store

DATA = {
    "identifier": "iPhone9,3",
    "firmwares": [
        {"identifier": "iPhone9,3", "version": "15.8.4", "buildid": "19H390",
         "url": "https://updates.cdn-apple.com/a/iPhone_15.8.4_19H390_Restore.ipsw",
         "filesize": 10, "sha256sum": "", "sha1sum": "", "signed": False, "releasedate": "2025-03-31T17:00:00Z"},
        {"identifier": "iPhone9,3", "version": "15.8.8", "buildid": "19H422",
         "url": "https://updates.cdn-apple.com/b/iPhone_15.8.8_19H422_Restore.ipsw",
         "filesize": 12, "sha256sum": "ab", "signed": True, "releasedate": "2026-09-15T17:00:00Z"},
        {"identifier": "iPhone9,3", "version": "10.0.1", "buildid": "14A403", "url": "https://x/old.ipsw",
         "signed": False},
    ],
}


def test_parse_and_sort_newest_first():
    fws = fwm.parse_firmwares(DATA)
    assert [f.version for f in fws] == ["15.8.8", "15.8.4", "10.0.1"]
    top = fws[0]
    assert top.signed and top.size == 12 and top.sha256 == "ab" and top.released == "2026-09-15"
    assert top.filename == "iPhone_15.8.8_19H422_Restore.ipsw" and top.label == "iOS 15.8.8 (19H422)"


def test_relation_to_installed_version():
    fws = {f.version: f for f in fwm.parse_firmwares(DATA)}
    assert fwm.relation(fws["15.8.8"], "15.8.4") == fwm.UPGRADE
    assert fwm.relation(fws["10.0.1"], "15.8.4") == fwm.DOWNGRADE
    assert fwm.relation(fws["15.8.4"], "15.8.4", "19H390") == fwm.CURRENT
    assert fwm.version_key("16.0") < fwm.version_key("16.0.1") < fwm.version_key("16.10")


def test_list_firmwares_uses_ipsw_me(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return DATA

    seen = []
    monkeypatch.setattr(fwm.requests, "get", lambda url, **k: seen.append(url) or R())
    assert [f.buildid for f in fwm.list_firmwares("iPhone9,3")] == ["19H422", "19H390", "14A403"]
    assert seen == ["https://api.ipsw.me/v4/device/iPhone9,3?type=ipsw"]


# ----------------------------------------------------------------- download
class Resp:
    def __init__(self, body: bytes, status=200):
        self.body, self.status_code = body, status
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise fwm.requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), 3):
            yield self.body[i:i + 3]


class Server:
    """Server di Apple simulato, con supporto a Range."""

    def __init__(self, content: bytes, ranges=True):
        self.content, self.ranges, self.requests = content, ranges, []

    def get(self, url, stream=True, timeout=None, headers=None):
        headers = headers or {}
        self.requests.append(headers)
        if "Range" in headers and self.ranges:
            start = int(headers["Range"].split("=")[1].rstrip("-"))
            return Resp(self.content[start:], 206)
        return Resp(self.content)


def make_fw(content: bytes):
    return fwm.Firmware("iPhone9,3", "15.8.8", "19H422", "https://apple.example/iPhone.ipsw",
                        size=len(content), sha256=hashlib.sha256(content).hexdigest(), signed=True)


def test_download_verifies_and_reports_progress(appdata):
    content = b"IPSW-CONTENT-0123456789"
    fw, server, seen = make_fw(content), Server(content), []
    path = fwm.download_ipsw(fw, progress_cb=lambda d, t: seen.append((d, t)), session=server)
    assert path == store.base_dir() / "ipsw" / "iPhone.ipsw" and path.read_bytes() == content
    assert seen[-2] == (len(content), len(content)) and seen[-1][0] == -1  # poi la verifica
    assert fwm.local_ipsw(fw) == path
    assert fwm.download_ipsw(fw, session=None) == path  # già scaricato: nessuna richiesta


def test_download_resumes_from_partial_file(appdata):
    content = b"0123456789ABCDEFGHIJ"
    fw, server = make_fw(content), Server(content)
    part = fwm.ipsw_dir() / "iPhone.ipsw.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(content[:8])
    path = fwm.download_ipsw(fw, session=server)
    assert server.requests == [{"Range": "bytes=8-"}] and path.read_bytes() == content


def test_download_restarts_if_server_ignores_range(appdata):
    content = b"0123456789ABCDEFGHIJ"
    fw, server = make_fw(content), Server(content, ranges=False)
    part = fwm.ipsw_dir() / "iPhone.ipsw.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"XXXXXXXX")
    assert fwm.download_ipsw(fw, session=server).read_bytes() == content


def test_corrupted_download_is_discarded(appdata):
    content = b"0123456789"
    fw = make_fw(content)
    with pytest.raises(fwm.FirmwareError, match="SHA256"):
        fwm.download_ipsw(fw, session=Server(b"9876543210"))
    assert not (fwm.ipsw_dir() / "iPhone.ipsw.part").exists() and fwm.local_ipsw(fw) is None


def test_cancel_keeps_partial_file(appdata):
    content = b"0123456789ABCDEFGHIJ"
    fw, calls = make_fw(content), []

    def stop():
        calls.append(1)
        return len(calls) > 2

    with pytest.raises(fwm.DownloadCancelled):
        fwm.download_ipsw(fw, should_stop=stop, session=Server(content))
    part = fwm.ipsw_dir() / "iPhone.ipsw.part"
    assert 0 < part.stat().st_size < len(content)


def test_incomplete_download(appdata):
    content = b"0123456789"
    with pytest.raises(fwm.FirmwareError, match="incompleto"):
        fwm.download_ipsw(make_fw(content), session=Server(content[:6]))


# ---------------------------------------------------------------- SHSH, libusb
def test_shsh_path_per_ecid(appdata):
    info = fwm.DeviceFirmwareInfo("UDID", "iPhone9,3", "d101ap", "15.8.4", "19H390", ecid=0x1A2B)
    assert info.ecid_hex == "0x1A2B"
    expected = store.base_dir() / "shsh" / "0x1A2B" / "iPhone9,3_15.8.8_19H422.shsh2"
    assert fwm.shsh_path(info, make_fw(b"x")) == expected


def test_unsigned_version_has_no_shsh(appdata):
    info = fwm.DeviceFirmwareInfo("UDID", "iPhone9,3", ecid=1)
    old = fwm.Firmware("iPhone9,3", "10.0.1", "14A403", "https://x/old.ipsw", signed=False)
    with pytest.raises(fwm.FirmwareError, match="non è più firmata"):
        fwm.save_shsh(info, old)


def test_libusb_detection(monkeypatch, appdata):
    import pymobiledevice3.usb as pusb

    monkeypatch.setattr(pusb, "list_devices", lambda: [])
    assert fwm.libusb_available()

    def missing():
        raise pusb.UsbError("libusb-1.0 was not found")

    monkeypatch.setattr(pusb, "list_devices", missing)
    assert fwm.libusb_available() is (fwm.os.name != "nt")


def test_models_only_ios_devices_newest_first():
    data = [{"identifier": "iPhone9,3", "name": "iPhone 7"}, {"identifier": "Watch6,1", "name": "Watch"},
            {"identifier": "iPhone17,3", "name": "iPhone 16"}, {"identifier": "iPad16,3", "name": "iPad Pro"},
            {"identifier": "iPod9,1", "name": "iPod touch 7"}, {"identifier": "Mac14,2", "name": "MacBook Air"},
            {"identifier": "iPhone10,6", "name": "iPhone X"}]
    models = fwm.parse_models(data)
    assert [m.identifier for m in models] == ["iPhone17,3", "iPhone10,6", "iPhone9,3", "iPad16,3", "iPod9,1"]
    assert models[0].family == "iPhone"


def test_local_shsh_lookup(appdata):
    fw = make_fw(b"x")
    assert fwm.local_shsh(fw) is None
    path = store.base_dir() / "shsh" / "0xABC" / "iPhone9,3_15.8.8_19H422.shsh2"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"blob")
    assert fwm.local_shsh(fw, 0xABC) == path and fwm.local_shsh(fw) == path
    assert fwm.local_shsh(fw, 0xDEF) is None
