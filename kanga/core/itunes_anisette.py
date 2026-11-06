"""Anisette 'iTunes' nativa: usa AOSKit.dll (AuthKit) di Apple tramite il
runtime Objective-C di iTunes per Windows.

Questa è la stessa strada di Sideloadly/AltServer-Windows: l'OTP viene
generato dalle DLL Apple ORIGINALI con lo stato ADI già provisionato
(C:\\ProgramData\\Apple Computer\\iTunes\\adi), quindi Apple la tratta come
un client Windows legittimo (AuthKitWin) e non la blocca come le anisette
emulate (-80045/-80046).

Sequenza (da AltServer-Windows / Sideloadly):
  LoadLibrary(objc.dll, Foundation.dll, AOSKit.dll)
  NSString.stringWithUTF8String:("-2")           -> DSID "no account"
  [AOSUtilities retrieveOTPHeadersForDSID:]      -> dict con X-Apple-MD / X-Apple-MD-M
  + X-Mme-Client-Info Windows/AuthKitWin, UUID device persistente, tempo UTC
"""
from __future__ import annotations

import base64
import ctypes
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .store import base_dir

logger = logging.getLogger(__name__)

# cartelle dove cercare il set DLL Apple (AOSKit + objc + Foundation)
CANDIDATE_DIRS = [
    r"C:\Program Files\iTunes\Internet Services",
    r"C:\Program Files (x86)\Common Files\Apple\Apple Application Support",
    r"C:\Program Files\Common Files\Apple\Apple Application Support",
    r"C:\Program Files\Common Files\Apple\Internet Services",
    r"C:\Program Files\3uTools9\iCloudDll",  # set Apple completo incluso AOSKit
]

# identità client Windows-AuthKit (identica a quella osservata in Sideloadly)
CLIENT_INFO = "<PC> <Windows;6.2(0,0);9200> <com.apple.AuthKitWin/1 (com.apple.iTunes/12.13.7)>"


OTP_MISSING_MSG = (
    "AOSKit non ha prodotto l'OTP (possibile errore -45075 'Info request failed'): "
    "il set di DLL Apple trovato è incompleto o incoerente. Serve il set ufficiale "
    "('Apple Application Support' + 'Internet Services' con AOSKit) installato da "
    "iTunes o iCloud per Windows."
)

class ItunesAnisetteError(RuntimeError):
    pass


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


def _dll_arch(path: Path) -> str:
    """Architettura di una DLL PE ('x86' | 'x64') senza dipendenze extra."""
    try:
        with open(path, "rb") as f:
            f.seek(0x3C)
            (pe_off,) = __import__("struct").unpack("<I", f.read(4))
            f.seek(pe_off + 4)
            (machine,) = __import__("struct").unpack("<H", f.read(2))
        return {0x14C: "x86", 0x8664: "x64"}.get(machine, "x64")
    except Exception:
        return "x64"


def _python_arch() -> str:
    import struct

    return "x64" if struct.calcsize("P") == 8 else "x86"


def find_apple_dll_dir() -> str:
    """Trova una cartella con AOSKit.dll + objc.dll + Foundation.dll coerenti."""
    for d in CANDIDATE_DIRS:
        if all((Path(d) / dll).exists() for dll in ("AOSKit.dll", "objc.dll", "Foundation.dll")):
            return d
    raise ItunesAnisetteError(
        "Non ho trovato AOSKit.dll/objc.dll/Foundation.dll. Installa iTunes "
        "classico (o iCloud per Windows) oppure indica la cartella con le DLL Apple."
    )


class ItunesAnisette:
    """Generatore di header anisette via AOSKit/ObjC di iTunes.

    Se l'architettura delle DLL non coincide con quella dell'interprete,
    usa l'helper 32-bit (kanga/tools/itunes_anisette32.py) come subprocess.
    """

    def __init__(self, lib_dir: str | None = None):
        self.lib_dir = lib_dir or find_apple_dll_dir()
        self._loaded = False
        arch = _dll_arch(Path(self.lib_dir) / "AOSKit.dll")
        self._use_helper = arch != _python_arch()
        self._helper_py32 = base_dir() / "py32" / "python.exe"
        logger.info(
            "Anisette iTunes: cartella DLL %s (dll %s, python %s, helper=%s)",
            self.lib_dir, arch, _python_arch(), self._use_helper,
        )

    # ------------------------------------------------------------ objc utils
    def _load(self):
        if self._loaded:
            return
        lib = Path(self.lib_dir)
        # le DLL Apple si risolvono a vicenda: directory attiva + dll search
        os.add_dll_directory(str(lib))
        ctypes.windll.kernel32.SetCurrentDirectoryW(str(lib))
        try:
            self.objc = ctypes.CDLL(str(lib / "objc.dll"))
            ctypes.CDLL(str(lib / "Foundation.dll"))
            self.aoskit = ctypes.CDLL(str(lib / "AOSKit.dll"))
        except OSError as e:
            raise ItunesAnisetteError(f"Caricamento DLL Apple fallito ({e}). "
                                      "Prova con un'altra cartella (es. quella di iTunes).") from e

        o = self.objc
        o.objc_getClass.restype = ctypes.c_void_p
        o.objc_getClass.argtypes = [ctypes.c_char_p]
        o.sel_registerName.restype = ctypes.c_void_p
        o.sel_registerName.argtypes = [ctypes.c_char_p]
        self._msgsend0 = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", o))
        self._msgsend_obj = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", o))
        self._msgsend_str = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(
            ("objc_msgSend", o))
        self._msgsend_utf8 = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", o))
        self._loaded = True

    def _cls(self, name: str) -> ctypes.c_void_p:
        c = self.objc.objc_getClass(name.encode())
        if not c:
            raise ItunesAnisetteError(f"Classe ObjC '{name}' non trovata in AOSKit")
        return c

    def _sel(self, name: str) -> ctypes.c_void_p:
        return self.objc.sel_registerName(name.encode())

    def _send0(self, obj, sel: str) -> ctypes.c_void_p:
        return self._msgsend0(obj, self._sel(sel))

    def _send_obj(self, obj, sel: str, arg) -> ctypes.c_void_p:
        return self._msgsend_obj(obj, self._sel(sel), arg)

    def _send_str(self, obj, sel: str, arg: str) -> ctypes.c_void_p:
        return self._msgsend_str(obj, self._sel(sel), arg.encode("utf-8"))

    def _to_str(self, obj) -> str:
        utf8 = self._msgsend_utf8(obj, self._sel("UTF8String"))
        return utf8.decode("utf-8", errors="replace") if utf8 else ""

    def _via_helper(self) -> dict[str, str]:
        """Anisette tramite il Python 32-bit (per DLL Apple x86)."""
        import json
        import subprocess

        helper = Path(__file__).resolve().parent.parent / "tools" / "itunes_anisette32.py"
        if not self._helper_py32.exists():
            raise ItunesAnisetteError(
                "Le DLL Apple disponibili sono a 32 bit ma manca il Python 32-bit "
                "helper (%s). Installa iTunes/iCloud 64-bit oppure riesegui l'app "
                "dopo aver preparato l'helper." % self._helper_py32
            )
        cmd = [str(self._helper_py32), str(helper), self.lib_dir]
        # niente finestra console quando l'app gira come GUI (pythonw)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=flags)
        out = proc.stdout.strip()
        try:
            data = json.loads(out.splitlines()[-1])
        except Exception:
            raise ItunesAnisetteError(f"Risposta inattesa dall'helper 32-bit: {out[:200]}")
        if "__error__" in data:
            err = str(data["__error__"])
            if "OTP/MID mancanti" in err:
                raise ItunesAnisetteError(f"{OTP_MISSING_MSG} (cartella DLL: {self.lib_dir})")
            raise ItunesAnisetteError(f"Helper 32-bit: {err}")
        return data

    # ------------------------------------------------------------ public
    def get_headers(self) -> dict[str, str]:
        """Header anisette completi (identità iTunes/AuthKitWin su Windows)."""
        if self._use_helper:
            return self._via_helper()
        self._load()

        nsstring = self._cls("NSString")
        make_str = lambda s: self._send_str(nsstring, "stringWithUTF8String:", s)

        aos_utilities = self._cls("AOSUtilities")
        dsid = make_str("-2")
        headers_dict = self._send_obj(aos_utilities, "retrieveOTPHeadersForDSID:", dsid)
        if not headers_dict:
            raise ItunesAnisetteError(
                "retrieveOTPHeadersForDSID ha restituito vuoto: lo stato ADI in "
                "C:\\ProgramData\\Apple Computer\\iTunes\\adi potrebbe mancare o essere "
                "corrotto. Apri iTunes ed effettua un login per rigenerarlo."
            )

        def dict_get(key: str) -> str:
            k = make_str(key)
            v = self._send_obj(headers_dict, "objectForKey:", k)
            return self._to_str(v) if v else ""

        otp = dict_get("X-Apple-MD")
        mid = dict_get("X-Apple-MD-M")
        if not otp or not mid:
            raise ItunesAnisetteError(OTP_MISSING_MSG)

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


def itunes_anisette_available() -> bool:
    try:
        find_apple_dll_dir()
        return True
    except ItunesAnisetteError:
        return False
