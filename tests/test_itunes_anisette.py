"""Anisette iTunes: ricerca delle DLL Apple, scelta del set funzionante, helper."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kanga.core import itunes_anisette as ia


def fake_dll(path: Path, machine: int = 0x8664) -> None:
    """DLL finta con un header PE minimo (basta per leggerne l'architettura)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(0x100)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\0\0"
    data[0x84:0x86] = machine.to_bytes(2, "little")
    path.write_bytes(bytes(data))


def itunes_icloud_layout(root: Path, machine: int = 0x8664) -> ia.AppleDllSet:
    """Disposizione di iTunes + iCloud installati dal sito Apple."""
    apple = root / "Apple"
    fake_dll(apple / "Internet Services" / "AOSKit.dll", machine)
    for name in ("objc.dll", "Foundation.dll"):
        fake_dll(apple / "Apple Application Support" / name, machine)
    return ia.AppleDllSet(apple / "Internet Services", apple / "Apple Application Support")


@pytest.fixture
def common_files(tmp_path, monkeypatch):
    root = tmp_path / "Common Files"
    monkeypatch.setenv("CommonProgramW6432", str(root))
    monkeypatch.setenv("CommonProgramFiles", str(root))
    monkeypatch.delenv("CommonProgramFiles(x86)", raising=False)
    monkeypatch.delenv("KANGA_APPLE_DLL_DIR", raising=False)
    return root


def test_finds_split_itunes_icloud_layout(common_files):
    expected = itunes_icloud_layout(common_files)
    sets = ia.find_apple_dll_sets()
    assert sets[0] == expected  # le DLL ufficiali hanno la precedenza (es. su 3uTools)
    assert expected.arch == "x64"


def test_incomplete_layout_is_ignored(common_files):
    fake_dll(common_files / "Apple" / "Internet Services" / "AOSKit.dll")  # manca objc/Foundation
    assert all(s.aoskit_dir != common_files / "Apple" / "Internet Services" for s in ia.find_apple_dll_sets())


def test_override_from_environment(tmp_path, common_files, monkeypatch):
    aos, sup = tmp_path / "aos", tmp_path / "sup"
    fake_dll(aos / "AOSKit.dll", 0x14C)
    fake_dll(sup / "objc.dll", 0x14C)
    fake_dll(sup / "Foundation.dll", 0x14C)
    monkeypatch.setenv("KANGA_APPLE_DLL_DIR", f"{aos};{sup}")
    first = ia.find_apple_dll_sets()[0]
    assert first == ia.AppleDllSet(aos, sup) and first.arch == "x86"


class FakeRunner:
    """Simula l'helper: risponde per cartella AOSKit."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        reply = self.replies[cmd[2]]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply) + "\n", stderr="")


def test_tries_sets_in_order_and_keeps_the_working_one(tmp_path, appdata):
    broken = itunes_icloud_layout(tmp_path / "a")
    good = itunes_icloud_layout(tmp_path / "b")
    runner = FakeRunner({
        str(broken.aoskit_dir): {"__error__": "RuntimeError: OTP/MID mancanti (otp=False mid=False)"},
        str(good.aoskit_dir): {"X-Apple-MD": "OTP", "X-Apple-MD-M": "MID"},
    })
    a = ia.ItunesAnisette([broken, good], runner=runner)
    h = a.get_headers()
    assert a.dll_set == good
    assert (h["X-Apple-I-MD"], h["X-Apple-I-MD-M"]) == ("OTP", "MID")
    assert h["X-Mme-Client-Info"] == ia.CLIENT_INFO
    assert len(h["X-Mme-Device-Id"]) == 36  # UUID persistente
    assert runner.calls[1][1] == str(ia.HELPER) and runner.calls[1][3] == str(good.support_dir)
    a.get_headers()
    assert len(runner.calls) == 3 and runner.calls[2][2] == str(good.aoskit_dir)  # niente nuovi tentativi
    assert a.get_headers()["X-Mme-Device-Id"] == h["X-Mme-Device-Id"]


def test_all_sets_failing_explains_how_to_fix(tmp_path, appdata):
    s = itunes_icloud_layout(tmp_path / "a")
    runner = FakeRunner({str(s.aoskit_dir): {"__error__": "RuntimeError: OTP/MID mancanti"}})
    with pytest.raises(ia.ItunesAnisetteError, match="Microsoft Store"):
        ia.ItunesAnisette([s], runner=runner).get_headers()


def test_no_dlls_found(monkeypatch):
    monkeypatch.setattr(ia, "find_apple_dll_sets", lambda: [])
    with pytest.raises(ia.ItunesAnisetteError, match="non trovate"):
        ia.ItunesAnisette()


def test_32bit_dlls_need_the_32bit_python(tmp_path, appdata):
    s = itunes_icloud_layout(tmp_path / "a", machine=0x14C)
    with pytest.raises(ia.ItunesAnisetteError, match="32 bit"):
        ia.ItunesAnisette([s], runner=FakeRunner({})).get_headers()


def test_helper_reports_errors_as_json(tmp_path):
    proc = subprocess.run([sys.executable, str(ia.HELPER), str(tmp_path / "nessuna")],
                          capture_output=True, text=True, timeout=60)
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 1 and "__error__" in data
