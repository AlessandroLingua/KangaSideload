"""Anisette 'iTunes' nativa: l'OTP viene generato da AOSKit.dll (AuthKit) di
Apple tramite il runtime Objective-C di iTunes/iCloud per Windows.

È la stessa strada di Sideloadly e AltServer-Windows: le DLL Apple ORIGINALI,
con lo stato ADI già provisionato (C:\\ProgramData\\Apple Computer\\iTunes\\adi),
fanno apparire Kanga come un client Windows legittimo (AuthKitWin), che Apple
non blocca come le anisette emulate (-80045/-80046, HTTP 503).

Dove stanno le DLL:
  - iTunes + iCloud per Windows scaricati dal SITO Apple (non dal Microsoft
    Store): objc.dll e Foundation.dll in «Common Files\\Apple\\Apple Application
    Support», AOSKit.dll in «Common Files\\Apple\\Internet Services»
  - set già pronti in un'unica cartella (es. 3uTools)
  - KANGA_APPLE_DLL_DIR="<dir AOSKit>[;<dir objc/Foundation>]" per forzarla

Le DLL vengono caricate da kanga/tools/itunes_anisette_helper.py in un processo
separato (Python a 64 bit o a 32 bit secondo le DLL): un crash delle DLL Apple
non chiude l'app. I set trovati vengono provati in ordine finché uno produce
l'OTP; quello funzionante resta in uso.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .store import base_dir

logger = logging.getLogger(__name__)

HELPER = Path(__file__).resolve().parent.parent / "tools" / "itunes_anisette_helper.py"

# identità client Windows-AuthKit (identica a quella osservata in Sideloadly)
CLIENT_INFO = "<PC> <Windows;6.2(0,0);9200> <com.apple.AuthKitWin/1 (com.apple.iTunes/12.13.7)>"

INSTALL_HINT = (
    "Installa iTunes (apple.com/itunes/download/win64) e iCloud per Windows "
    "scaricati dal sito Apple, NON dal Microsoft Store: le versioni dello Store "
    "non espongono le DLL necessarie."
)
NOT_FOUND_MSG = "DLL Apple (AOSKit.dll, objc.dll, Foundation.dll) non trovate. " + INSTALL_HINT
OTP_MISSING_MSG = (
    "AOSKit non ha prodotto l'OTP (possibile errore -45075 'Info request failed'): "
    "il set di DLL Apple è incompleto o incoerente. " + INSTALL_HINT
)


class ItunesAnisetteError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppleDllSet:
    aoskit_dir: Path   # contiene AOSKit.dll
    support_dir: Path  # contiene objc.dll e Foundation.dll

    def complete(self) -> bool:
        return ((self.aoskit_dir / "AOSKit.dll").is_file()
                and all((self.support_dir / n).is_file() for n in ("objc.dll", "Foundation.dll")))

    @property
    def arch(self) -> str:
        return dll_arch(self.aoskit_dir / "AOSKit.dll")

    def __str__(self) -> str:
        if self.aoskit_dir == self.support_dir:
            return str(self.aoskit_dir)
        return f"{self.aoskit_dir} + {self.support_dir}"


def dll_arch(path: Path) -> str:
    """Architettura di una DLL PE ('x86' | 'x64') leggendo l'header."""
    try:
        with open(path, "rb") as f:
            f.seek(0x3C)
            (pe_off,) = struct.unpack("<I", f.read(4))
            f.seek(pe_off + 4)
            (machine,) = struct.unpack("<H", f.read(2))
        return {0x14C: "x86", 0x8664: "x64"}.get(machine, "x64")
    except Exception:
        return "x64"


def python_arch() -> str:
    return "x64" if struct.calcsize("P") == 8 else "x86"


def _common_files_roots() -> list[Path]:
    roots = [os.environ.get(v) for v in ("CommonProgramW6432", "CommonProgramFiles", "CommonProgramFiles(x86)")]
    roots += [r"C:\Program Files\Common Files", r"C:\Program Files (x86)\Common Files"]
    return [Path(r) for r in dict.fromkeys(r for r in roots if r)]


def candidate_sets() -> list[AppleDllSet]:
    """Tutte le posizioni note, in ordine di preferenza (anche se non esistono)."""
    out: list[AppleDllSet] = []
    override = os.environ.get("KANGA_APPLE_DLL_DIR", "").strip()
    if override:
        parts = [p.strip() for p in override.split(";") if p.strip()]
        out.append(AppleDllSet(Path(parts[0]), Path(parts[-1])))
    for root in _common_files_roots():
        apple = root / "Apple"
        support = apple / "Apple Application Support"
        out.append(AppleDllSet(apple / "Internet Services", support))  # iTunes + iCloud dal sito Apple
        out.append(AppleDllSet(support, support))
    for prog in (r"C:\Program Files", r"C:\Program Files (x86)"):
        out.append(AppleDllSet(Path(prog) / "iTunes" / "Internet Services",
                               Path(prog) / "Common Files" / "Apple" / "Apple Application Support"))
    out.append(AppleDllSet(Path(r"C:\Program Files\3uTools9\iCloudDll"),
                           Path(r"C:\Program Files\3uTools9\iCloudDll")))
    return list(dict.fromkeys(out))


def find_apple_dll_sets() -> list[AppleDllSet]:
    """Set di DLL completi presenti su questo computer, in ordine di preferenza."""
    return [s for s in candidate_sets() if s.complete()]


def _device_id() -> str:
    """UUID persistente del 'device' (X-Mme-Device-Id / X-Apple-I-MD-LU)."""
    base = base_dir()
    base.mkdir(parents=True, exist_ok=True)
    f = base / "device-id.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    did = str(uuid.uuid4()).upper()
    f.write_text(did, encoding="utf-8")
    return did


def build_headers(otp: str, mid: str) -> dict[str, str]:
    did = _device_id()
    now = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "X-Apple-I-MD": otp,
        "X-Apple-I-MD-M": mid,
        "X-Apple-I-MD-LU": base64.b64encode(did.encode()).decode(),
        "X-Apple-I-MD-RINFO": "17106176",
        "X-Mme-Device-Id": did,
        "X-Apple-I-SRL-NO": "0",
        "X-Mme-Client-Info": CLIENT_INFO,
        "X-Apple-I-Client-Time": now,
        "X-Apple-Locale": "en_US",
        "X-Apple-I-TimeZone": "PST",
    }


class ItunesAnisette:
    """Header anisette dalle DLL Apple di iTunes/iCloud (via helper)."""

    def __init__(self, dll_sets: list[AppleDllSet] | None = None, runner=None):
        self._sets = list(dll_sets) if dll_sets is not None else find_apple_dll_sets()
        if not self._sets:
            raise ItunesAnisetteError(NOT_FOUND_MSG)
        self._run = runner or subprocess.run
        self.dll_set: AppleDllSet | None = None  # il set che funziona, scelto al primo uso

    @staticmethod
    def _python_for(arch: str) -> Path:
        if arch == python_arch():
            exe = Path(sys.executable)
            # avviata con doppio clic l'app gira in pythonw.exe: per leggere l'output
            # dell'helper serve il python.exe "da console" accanto (la finestra resta nascosta)
            console = exe.with_name("python.exe")
            return console if exe.name.lower() == "pythonw.exe" and console.is_file() else exe
        py32 = base_dir() / "py32" / "python.exe"
        if arch == "x86" and py32.is_file():
            return py32
        raise ItunesAnisetteError(
            f"Le DLL Apple sono a {'32' if arch == 'x86' else '64'} bit e serve un Python della "
            f"stessa architettura ({py32} per quelle a 32 bit). Con iTunes e iCloud a 64 bit "
            "dal sito Apple non serve.")

    def _otp(self, dll_set: AppleDllSet) -> tuple[str, str]:
        cmd = [str(self._python_for(dll_set.arch)), str(HELPER),
               str(dll_set.aoskit_dir), str(dll_set.support_dir)]
        # niente finestra console quando l'app gira come GUI
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=60, creationflags=flags)
        except subprocess.TimeoutExpired as e:
            raise ItunesAnisetteError("Le DLL Apple non hanno risposto entro 60 secondi") from e
        out = (proc.stdout or "").strip()
        try:
            data = json.loads(out.splitlines()[-1])
        except Exception:
            detail = out or (proc.stderr or "").strip() or f"exit {proc.returncode}"
            raise ItunesAnisetteError(f"Risposta inattesa dalle DLL Apple: {detail[:200]}") from None
        if "__error__" in data:
            err = str(data["__error__"])
            raise ItunesAnisetteError(OTP_MISSING_MSG if "OTP/MID mancanti" in err else err)
        return data["X-Apple-MD"], data["X-Apple-MD-M"]

    def get_headers(self) -> dict[str, str]:
        if self.dll_set is not None:
            return build_headers(*self._otp(self.dll_set))
        errors = []
        for s in self._sets:
            try:
                otp, mid = self._otp(s)
            except ItunesAnisetteError as e:
                logger.info("Anisette iTunes: %s non funziona (%s)", s, e)
                errors.append(f"{s}: {e}")
                continue
            self.dll_set = s
            logger.info("Anisette iTunes: uso le DLL Apple in %s (%s)", s, s.arch)
            return build_headers(otp, mid)
        if len(errors) == 1:
            raise ItunesAnisetteError(errors[0])
        raise ItunesAnisetteError("Nessun set di DLL Apple funziona — " + " | ".join(errors))


def itunes_anisette_available() -> bool:
    return bool(find_apple_dll_sets())
