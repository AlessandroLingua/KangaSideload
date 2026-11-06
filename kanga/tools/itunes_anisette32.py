"""Helper 32-bit per l'anisette iTunes: carica le DLL Apple x86 (AOSKit/objc/
Foundation) e stampa gli header anisette in JSON su stdout.

Chiamato dalla app 64-bit come subprocess: python32 itunes_anisette32.py <libdir>
Uso minimo di librerie (solo stdlib) per funzionare nell'embeddable.
"""
import base64
import ctypes
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

CLIENT_INFO = "<PC> <Windows;6.2(0,0);9200> <com.apple.AuthKitWin/1 (com.apple.iTunes/12.13.7)>"
DEVICE_ID_PATH = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "Kanga" / "device-id.txt"


def device_id() -> str:
    DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DEVICE_ID_PATH.exists():
        return DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
    did = str(uuid.uuid4()).upper()
    DEVICE_ID_PATH.write_text(did, encoding="utf-8")
    return did


def main():
    lib_dir = sys.argv[1]
    os.add_dll_directory(lib_dir)
    ctypes.windll.kernel32.SetCurrentDirectoryW(lib_dir)

    objc = ctypes.CDLL(os.path.join(lib_dir, "objc.dll"))
    ctypes.CDLL(os.path.join(lib_dir, "Foundation.dll"))
    ctypes.CDLL(os.path.join(lib_dir, "AOSKit.dll"))

    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    send0 = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    send_obj = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    send_str = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(("objc_msgSend", objc))
    send_utf8 = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))

    def cls(name):
        c = objc.objc_getClass(name.encode())
        if not c:
            raise RuntimeError(f"classe ObjC {name} non trovata")
        return c

    def sel(name):
        return objc.sel_registerName(name.encode())

    def make_str(s):
        return send_str(cls("NSString"), sel("stringWithUTF8String:"), s.encode())

    def to_str(o):
        utf8 = send_utf8(o, sel("UTF8String"))
        return utf8.decode("utf-8", "replace") if utf8 else ""

    dsid = make_str("-2")
    headers = send_obj(cls("AOSUtilities"), sel("retrieveOTPHeadersForDSID:"), dsid)
    if not headers:
        raise RuntimeError("retrieveOTPHeadersForDSID vuoto")

    def dget(key):
        v = send_obj(headers, sel("objectForKey:"), make_str(key))
        return to_str(v) if v else ""

    otp = dget("X-Apple-MD")
    mid = dget("X-Apple-MD-M")
    if not otp or not mid:
        raise RuntimeError(f"OTP/MID mancanti (otp={bool(otp)} mid={bool(mid)})")

    did = device_id()
    now = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {
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
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"__error__": f"{e.__class__.__name__}: {e}"}))
        sys.exit(1)
