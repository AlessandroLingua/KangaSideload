"""Lettura dei metadati di un file .ipa: nome, versione, bundle id, icona.

Le icone dentro gli IPA sono quasi sempre PNG "CgBI" (formato ottimizzato da
Xcode: canali BGRA, alpha premoltiplicato, deflate senza header zlib) che i
normali decoder (Qt compreso) non sanno leggere: `normalize_png` le converte
in PNG standard, in puro Python.
"""
from __future__ import annotations

import plistlib
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# oltre questa dimensione non decodifichiamo icone CgBI (conversione in puro Python)
_MAX_CGBI_PIXELS = 512 * 512
_MAX_ICON_BYTES = 2 * 1024 * 1024


class IpaError(ValueError):
    """Il file non è un IPA valido o leggibile."""


@dataclass
class IpaInfo:
    path: Path
    size: int
    bundle_id: str
    name: str
    version: str = ""
    build: str = ""
    min_os: str = ""
    executable: str = ""
    extensions: int = 0
    icon_png: bytes | None = None

    @property
    def version_label(self) -> str:
        if self.version and self.build and self.build != self.version:
            return f"{self.version} ({self.build})"
        return self.version or self.build


def find_main_app(names) -> tuple[str, str] | None:
    """(cartella app, percorso Info.plist) dell'app principale: Payload/X.app/Info.plist."""
    for n in names:
        parts = n.split("/")
        if (
            len(parts) == 3
            and parts[0] == "Payload"
            and parts[1].endswith(".app")
            and parts[2] == "Info.plist"
        ):
            return f"Payload/{parts[1]}", n
    return None


def read_ipa_info(path) -> IpaInfo:
    """Legge i metadati principali dell'IPA. Solleva IpaError se il file non è valido."""
    path = Path(path)
    if not path.is_file():
        raise IpaError(f"File non trovato: {path}")
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise IpaError("Il file non è un .ipa valido (archivio illeggibile).") from e

    with zf:
        names = zf.namelist()
        found = find_main_app(names)
        if not found:
            raise IpaError("Il file non contiene un'app iOS (manca Payload/*.app/Info.plist).")
        app_dir, info_name = found
        try:
            info = plistlib.loads(zf.read(info_name))
        except Exception as e:
            raise IpaError("L'Info.plist dell'app è illeggibile.") from e
        if not isinstance(info, dict):
            raise IpaError("L'Info.plist dell'app non è valido.")

        bundle_id = info.get("CFBundleIdentifier")
        if not bundle_id:
            raise IpaError("CFBundleIdentifier mancante nell'Info.plist.")

        name = (
            info.get("CFBundleDisplayName")
            or info.get("CFBundleName")
            or PurePosixPath(app_dir).stem
        )
        extensions = sum(
            1
            for n in names
            if n.startswith(f"{app_dir}/PlugIns/")
            and n.endswith(".appex/Info.plist")
            and n.count("/") == 4
        )
        try:
            icon = _extract_icon(zf, names, app_dir, info)
        except Exception:
            icon = None  # l'icona è solo estetica: mai bloccante

    return IpaInfo(
        path=path,
        size=path.stat().st_size,
        bundle_id=str(bundle_id),
        name=str(name),
        version=str(info.get("CFBundleShortVersionString", "") or ""),
        build=str(info.get("CFBundleVersion", "") or ""),
        min_os=str(info.get("MinimumOSVersion", "") or ""),
        executable=str(info.get("CFBundleExecutable", "") or ""),
        extensions=extensions,
        icon_png=icon,
    )


# --------------------------------------------------------------------- icona
def _icon_base_names(info: dict) -> list[str]:
    names: list[str] = []
    for key in ("CFBundleIcons", "CFBundleIcons~ipad"):
        icons = info.get(key)
        primary = icons.get("CFBundlePrimaryIcon") if isinstance(icons, dict) else None
        if isinstance(primary, dict):
            files = primary.get("CFBundleIconFiles") or []
            names.extend(f for f in files if isinstance(f, str))
            icon_name = primary.get("CFBundleIconName")
            if isinstance(icon_name, str):
                names.append(icon_name)
    legacy = info.get("CFBundleIconFiles") or []
    if isinstance(legacy, list):
        names.extend(f for f in legacy if isinstance(f, str))
    single = info.get("CFBundleIconFile")
    if isinstance(single, str):
        names.append(single)

    out: list[str] = []
    for n in names:
        base = n[:-4] if n.lower().endswith(".png") else n
        if base and base not in out:
            out.append(base)
    return out


def _extract_icon(zf: zipfile.ZipFile, names, app_dir: str, info: dict) -> bytes | None:
    prefix = f"{app_dir}/"
    root_pngs = [
        zf.getinfo(n)
        for n in names
        if n.startswith(prefix)
        and "/" not in n[len(prefix):]
        and n.lower().endswith(".png")
    ]
    root_pngs = [z for z in root_pngs if 0 < z.file_size <= _MAX_ICON_BYTES]
    if not root_pngs:
        return None

    bases = _icon_base_names(info)
    candidates = [
        z for z in root_pngs if any(PurePosixPath(z.filename).name.startswith(b) for b in bases)
    ]
    if not candidates:
        candidates = [z for z in root_pngs if "icon" in PurePosixPath(z.filename).name.lower()]
    # dal più grande al più piccolo: il primo che si riesce a decodificare
    for zinfo in sorted(candidates, key=lambda z: z.file_size, reverse=True):
        png = normalize_png(zf.read(zinfo))
        if png:
            return png
    return None


# ------------------------------------------------------------- PNG / CgBI
def _iter_chunks(data: bytes):
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        yield ctype, body
        pos += 12 + length
        if ctype == b"IEND":
            break


def is_cgbi_png(data: bytes) -> bool:
    return data.startswith(PNG_SIGNATURE) and data[12:16] == b"CgBI"


def normalize_png(data: bytes) -> bytes | None:
    """PNG standard da un PNG qualsiasi (anche CgBI). None se non decodificabile."""
    if not data or not data.startswith(PNG_SIGNATURE):
        return None
    if not is_cgbi_png(data):
        return data
    try:
        return _decode_cgbi(list(_iter_chunks(data)))
    except (zlib.error, struct.error, IndexError, ValueError):
        return None


def _decode_cgbi(chunks) -> bytes | None:
    ihdr = next((body for ctype, body in chunks if ctype == b"IHDR"), None)
    if ihdr is None or len(ihdr) < 13:
        return None
    width, height, depth, color, _comp, _filter, interlace = struct.unpack(">IIBBBBB", ihdr[:13])
    if depth != 8 or color not in (2, 6) or interlace != 0:
        return None
    if width == 0 or height == 0 or width * height > _MAX_CGBI_PIXELS:
        return None

    bpp = 4 if color == 6 else 3
    stride = width * bpp
    compressed = b"".join(body for ctype, body in chunks if ctype == b"IDAT")
    raw = zlib.decompress(compressed, -15)  # deflate "nudo", senza header zlib
    if len(raw) < height * (stride + 1):
        return None

    px = _unfilter(raw, height, stride, bpp)
    # BGR(A) -> RGB(A)
    px[0::bpp], px[2::bpp] = px[2::bpp], px[0::bpp]
    if bpp == 4:
        _unpremultiply(px)
    return encode_png(width, height, color, bytes(px))


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += stride + 1
        if ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                line[i] = (line[i] + pred) & 0xFF
        elif ftype != 0:
            raise ValueError(f"filtro PNG sconosciuto: {ftype}")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return out


def _unpremultiply(px: bytearray) -> None:
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if a == 0 or a == 255:
            continue
        half = a // 2
        px[i] = min(255, (px[i] * 255 + half) // a)
        px[i + 1] = min(255, (px[i + 1] * 255 + half) // a)
        px[i + 2] = min(255, (px[i + 2] * 255 + half) // a)


def encode_png(width: int, height: int, color: int, pixels: bytes) -> bytes:
    """Codifica un PNG standard (8 bit, filtro 0) da pixel RGB/RGBA grezzi."""
    bpp = 4 if color == 6 else 3
    stride = width * bpp
    raw = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(ctype: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(ctype + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
