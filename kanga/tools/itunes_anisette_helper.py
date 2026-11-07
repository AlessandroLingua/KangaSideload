"""Helper anisette iTunes: carica le DLL Apple (objc, Foundation, AOSKit) e
stampa su stdout, in JSON, l'OTP e il machine id generati da AOSKit.

Gira in un processo separato, con un Python della stessa architettura delle
DLL (64 bit: quello dell'app; 32 bit: %LOCALAPPDATA%/Kanga/py32): un crash
delle DLL Apple non può chiudere l'app e la cartella corrente dell'app non
cambia. Solo libreria standard, per funzionare anche nel Python embeddable.

Uso: python itunes_anisette_helper.py <cartella AOSKit.dll> [<cartella objc.dll/Foundation.dll>]
Output: {"X-Apple-MD": ..., "X-Apple-MD-M": ...} oppure {"__error__": "..."}
"""
import ctypes
import json
import os
import sys


def otp_headers(aoskit_dir: str, support_dir: str) -> dict:
    # le DLL Apple si risolvono a vicenda: cartelle nel percorso di ricerca e
    # cartella corrente su "Apple Application Support" (come fa AltServer)
    keep = [os.add_dll_directory(d) for d in dict.fromkeys((support_dir, aoskit_dir))]
    os.environ["PATH"] = os.pathsep.join((support_dir, aoskit_dir, os.environ.get("PATH", "")))
    ctypes.windll.kernel32.SetCurrentDirectoryW(support_dir)

    objc = ctypes.CDLL(os.path.join(support_dir, "objc.dll"))
    ctypes.CDLL(os.path.join(support_dir, "Foundation.dll"))
    ctypes.CDLL(os.path.join(aoskit_dir, "AOSKit.dll"))

    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send_obj = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", objc))
    send_str = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(
        ("objc_msgSend", objc))
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

    # DSID "-2" = nessun account: header anisette della macchina
    headers = send_obj(cls("AOSUtilities"), sel("retrieveOTPHeadersForDSID:"), make_str("-2"))
    if not headers:
        raise RuntimeError("retrieveOTPHeadersForDSID vuoto")

    def dget(key):
        v = send_obj(headers, sel("objectForKey:"), make_str(key))
        return to_str(v) if v else ""

    otp, mid = dget("X-Apple-MD"), dget("X-Apple-MD-M")
    if not otp or not mid:
        raise RuntimeError(f"OTP/MID mancanti (otp={bool(otp)} mid={bool(mid)})")
    del keep
    return {"X-Apple-MD": otp, "X-Apple-MD-M": mid}


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"__error__": "uso: itunes_anisette_helper.py <dir AOSKit> [<dir objc>]"}))
        return 2
    aoskit_dir = sys.argv[1]
    support_dir = sys.argv[2] if len(sys.argv) > 2 else aoskit_dir
    try:
        print(json.dumps(otp_headers(aoskit_dir, support_dir)))
        return 0
    except Exception as e:
        print(json.dumps({"__error__": f"{e.__class__.__name__}: {e}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
