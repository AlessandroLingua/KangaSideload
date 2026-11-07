"""Oggetti finti e generatori di file per i test (nessun iPhone, nessun server Apple)."""
from __future__ import annotations

import plistlib
import struct
import threading
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kanga.core.appflow import STEPS, SideloadResult
from kanga.core.device import DeviceInfo
from kanga.core.gsa import AppleSession, GsaError
from kanga.core.ipa import PNG_SIGNATURE

# ------------------------------------------------------------------ Mach-O
MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
MH_EXECUTE = 0x2
LC_SEGMENT_64 = 0x19
LC_MAIN = 0x80000028
MH_NOUNDEFS = 0x1
MH_TWOLEVEL = 0x80
MH_PIE = 0x20000


def build_minimal_macho() -> bytes:
    """Mach-O ARM64 minimale ma strutturalmente valido:
    header + __TEXT (con __text) + LC_MAIN + __LINKEDIT, con padding
    nell'header per il load command LC_CODE_SIGNATURE che zsign inserirà.
    """
    file_size = 16 * 1024
    text_cmdsize = 72 + 80
    linkedit_cmdsize = 72
    main_cmdsize = 24
    header_size = 32
    ncmds = 3
    sizeofcmds = text_cmdsize + linkedit_cmdsize + main_cmdsize
    header_pad = 128
    linkedit_off = 12 * 1024

    header = struct.pack(
        "<IIIIIIiI", MH_MAGIC_64, CPU_TYPE_ARM64, 0, MH_EXECUTE, ncmds, sizeofcmds,
        MH_NOUNDEFS | MH_TWOLEVEL | MH_PIE, 0,
    )
    seg = struct.pack(
        "<II16sQQQQiiII", LC_SEGMENT_64, text_cmdsize, b"__TEXT", 0, linkedit_off, 0,
        linkedit_off, 0x5, 0x5, 1, 0,
    )
    text_start = header_size + sizeofcmds + header_pad
    sect = struct.pack(
        "<16s16sQQIIiiIIII", b"__text", b"__TEXT", text_start, linkedit_off - text_start,
        text_start, 4, 0, 0, 0, 0, 0, 0,
    )
    main_lc = struct.pack("<IIQQ", LC_MAIN, main_cmdsize, text_start, 0)
    linkedit = struct.pack(
        "<II16sQQQQiiII", LC_SEGMENT_64, linkedit_cmdsize, b"__LINKEDIT", linkedit_off,
        file_size - linkedit_off, linkedit_off, file_size - linkedit_off, 0x1, 0x1, 0, 0,
    )
    body = header + seg + sect + main_lc + linkedit
    out = bytearray(file_size)
    out[: len(body)] = body
    return bytes(out)


# --------------------------------------------------------------------- PNG
def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_row(ftype: int, line: bytes, prev: bytes, bpp: int) -> bytes:
    out = bytearray(len(line))
    for i in range(len(line)):
        a = line[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        pred = {0: 0, 1: a, 2: b, 3: (a + b) >> 1, 4: _paeth(a, b, c)}[ftype]
        out[i] = (line[i] - pred) & 0xFF
    return bytes(out)


def encode_cgbi(width: int, height: int, rgba: bytes, filters=(0, 1, 2, 3, 4)) -> bytes:
    """PNG in formato Apple CgBI: BGRA, alpha premoltiplicato, deflate nudo, filtri misti."""
    px = bytearray(rgba)
    for i in range(0, len(px), 4):
        a = px[i + 3]
        for k in range(3):
            px[i + k] = (px[i + k] * a + 127) // 255
    px[0::4], px[2::4] = px[2::4], px[0::4]  # RGBA -> BGRA
    stride = width * 4
    raw = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = bytes(px[y * stride : (y + 1) * stride])
        ftype = filters[y % len(filters)]
        raw += bytes([ftype]) + _filter_row(ftype, line, prev, 4)
        prev = line
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    idat = comp.compress(bytes(raw)) + comp.flush()
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"CgBI", b"\x50\x00\x20\x06")
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def sample_icon_rgba(size: int = 60) -> bytes:
    """Icona di prova: sfondo a gradiente con un 'obiettivo' bianco al centro."""
    out = bytearray()
    c = (size - 1) / 2
    for y in range(size):
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            if d < size * 0.22:
                out += bytes([245, 245, 250, 255])
            elif d < size * 0.30:
                out += bytes([30, 30, 46, 255])
            else:
                t = (x + y) / (2 * size)
                out += bytes([int(255 - 90 * t), int(94 + 40 * t), int(58 + 150 * t), 255])
    return bytes(out)


# --------------------------------------------------------------------- IPA
def make_ipa(dest: Path, name: str = "KangaTest", bundle: str = "com.kanga.test",
             version: str = "1.2", build: str = "34", icon: bool = False,
             extensions: int = 0, cgbi: bool = True) -> Path:
    exe = name.replace(" ", "")
    info = {
        "CFBundleExecutable": exe,
        "CFBundleIdentifier": bundle,
        "CFBundleName": exe,
        "CFBundleDisplayName": name,
        "CFBundleVersion": build,
        "CFBundleShortVersionString": version,
        "CFBundlePackageType": "APPL",
        "MinimumOSVersion": "14.0",
        "CFBundleSupportedPlatforms": ["iPhoneOS"],
    }
    if icon:
        info["CFBundleIcons"] = {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}}
    app = f"Payload/{exe}.app"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{app}/{exe}", build_minimal_macho())
        z.writestr(f"{app}/Info.plist", plistlib.dumps(info))
        z.writestr(f"{app}/PkgInfo", b"APPL????")
        if icon:
            small = encode_cgbi(60, 60, sample_icon_rgba(60)) if cgbi else _plain_png(60)
            big = encode_cgbi(120, 120, sample_icon_rgba(120)) if cgbi else _plain_png(120)
            z.writestr(f"{app}/AppIcon60x60@2x.png", big)
            z.writestr(f"{app}/AppIcon60x60.png", small)
        for i in range(extensions):
            ext = f"{app}/PlugIns/Ext{i}.appex"
            z.writestr(f"{ext}/Info.plist", plistlib.dumps({"CFBundleIdentifier": f"{bundle}.ext{i}"}))
    return dest


def _plain_png(size: int) -> bytes:
    from kanga.core.ipa import encode_png

    return encode_png(size, size, 6, sample_icon_rgba(size))


# ------------------------------------------------------------- dispositivi
class FakeDevices:
    """Backend dispositivi finto con la stessa interfaccia di kanga.core.device."""

    def __init__(self, devices=(), usbmux: bool = True, pair_error: Exception | None = None):
        self.devices = list(devices)
        self.usbmux = usbmux
        self.pair_error = pair_error
        self.pair_calls: list[str] = []

    def usbmux_available(self) -> bool:
        return self.usbmux

    def list_devices(self) -> list[DeviceInfo]:
        return list(self.devices)

    def list_serials(self) -> list[str]:
        if not self.usbmux:
            raise RuntimeError("usbmux assente")
        return sorted(d.udid for d in self.devices)

    def pair(self, udid: str) -> DeviceInfo:
        self.pair_calls.append(udid)
        if self.pair_error:
            raise self.pair_error
        for d in self.devices:
            if d.udid == udid:
                d.trusted, d.status = True, "ok"
                return d
        raise RuntimeError("device non trovato")


# ------------------------------------------------------------------- flusso
class FakeGsaClient:
    def __init__(self, valid_code: str = "123456"):
        self.valid_code = valid_code
        self.triggered = 0
        self.submitted: list[str] = []

    def trigger_2fa(self):
        self.triggered += 1

    def submit_2fa_code(self, code: str):
        self.submitted.append(code)
        if code != self.valid_code:
            raise GsaError("Codice 2FA rifiutato (HTTP 401). Riprova con un nuovo codice.")


class FakeFlow:
    """AppFlow finto: login (con 2FA opzionale) e sideload simulati."""

    def __init__(self, logged_in: bool = False, name: str = "", require_2fa: bool = False,
                 login_error: Exception | None = None, sideload_error: Exception | None = None):
        self.on_event = lambda tag, data: None
        self.logged_in = logged_in
        self.name = name
        self.require_2fa = require_2fa
        self.login_error = login_error
        self.sideload_error = sideload_error
        self.gsa = FakeGsaClient()
        self.login_calls: list[tuple[str, str]] = []
        self.sideload_calls: list[dict] = []
        self.sideload_thread: threading.Thread | None = None

    # --- sessione
    def has_session(self) -> bool:
        return self.logged_in

    def account_name(self) -> str:
        return self.name if self.logged_in else ""

    def restore_session(self):
        return AppleSession(xcode_token="token", adsid="000123", dsid="1") if self.logged_in else None

    def logout(self):
        self.logged_in = False

    def login(self, apple_id, password, on_2fa=None):
        self.login_calls.append((apple_id, password))
        if self.login_error:
            raise self.login_error
        if self.require_2fa:
            on_2fa(self.gsa)
        self.logged_in = True
        self.name = apple_id
        return self.restore_session()

    # --- sideload
    def sideload(self, session, ipa_path, udid, out_dir=None, custom_name=None, device_name=None):
        self.sideload_thread = threading.current_thread()
        self.sideload_calls.append(
            {"ipa": Path(ipa_path), "udid": udid, "custom_name": custom_name, "device_name": device_name}
        )
        for key, _label in STEPS:
            self.on_event("step", key)
            self.on_event("log", f"passo {key}")
            if key == "install":
                self.on_event("progress", (None, "Caricamento sul dispositivo"))
                self.on_event("progress", (60, "Installing"))
            if self.sideload_error and key == "sign":
                raise self.sideload_error
        self.on_event("step", "done")
        signed = Path(ipa_path).with_name(Path(ipa_path).stem + "-kanga.ipa")
        return SideloadResult(
            signed_ipa=signed,
            bundle_id="com.example.ABCDEapp",
            original_bundle_id="com.example.app",
            app_name=custom_name or "App",
            udid=udid,
            team_name="Mario Rossi",
            expires=datetime.now(timezone.utc) + timedelta(days=7),
        )


# --------------------------------------------------------- certificati Apple
def self_signed_cert(key_pem: bytes, cn: str = "Kanga Dev") -> bytes:
    """Certificato (DER) firmato con la chiave indicata: simula quello emesso da Apple."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID

    key = serialization.load_pem_private_key(key_pem, password=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


def new_key_pem() -> bytes:
    from kanga.core.apple_services import generate_csr_and_key

    return generate_csr_and_key()[1]


def fake_mobileprovision(team_id: str, bundle_id: str, expires: datetime) -> bytes:
    """Un .mobileprovision è un CMS con dentro un plist: basta il plist tra byte 'binari'."""
    plist = plistlib.dumps({
        "Name": "iOS Team Provisioning Profile",
        "ExpirationDate": expires.replace(tzinfo=None),
        "TeamIdentifier": [team_id],
        "Entitlements": {
            "application-identifier": f"{team_id}.{bundle_id}",
            "com.apple.developer.team-identifier": team_id,
            "get-task-allow": True,
        },
    })
    return b"\x30\x82\x10\x00CMS-HEADER" + plist + b"\x00\x01CMS-SIGNATURE"
