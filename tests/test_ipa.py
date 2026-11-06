"""Metadati IPA e conversione delle icone CgBI."""
import plistlib
import struct
import zipfile
import zlib

import pytest

from fakes import encode_cgbi, make_ipa
from kanga.core import signer
from kanga.core.ipa import (
    PNG_SIGNATURE,
    IpaError,
    encode_png,
    find_main_app,
    is_cgbi_png,
    normalize_png,
    read_ipa_info,
)


def decode_plain_png(data: bytes) -> tuple[int, int, bytes]:
    """Decoder minimale per i PNG prodotti da encode_png (8 bit RGBA, filtro 0)."""
    assert data.startswith(PNG_SIGNATURE)
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]
        assert crc == zlib.crc32(ctype + body) & 0xFFFFFFFF, "CRC chunk errato"
        if ctype == b"IHDR":
            ihdr = body
        elif ctype == b"IDAT":
            idat += body
        pos += 12 + length
    w, h, depth, color = struct.unpack(">IIBB", ihdr[:10])
    assert depth == 8 and color == 6
    raw = zlib.decompress(idat)
    stride = w * 4
    rows = []
    for y in range(h):
        assert raw[y * (stride + 1)] == 0
        rows.append(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
    return w, h, b"".join(rows)


def test_read_ipa_info(tmp_path):
    ipa = make_ipa(tmp_path / "App.ipa", name="Fotocamera Pro", bundle="com.example.camera",
                   version="2.5", build="91", icon=True)
    info = read_ipa_info(ipa)
    assert info.bundle_id == "com.example.camera"
    assert info.name == "Fotocamera Pro"
    assert info.version_label == "2.5 (91)"
    assert info.min_os == "14.0"
    assert info.executable == "FotocameraPro"
    assert info.extensions == 0
    assert info.size == ipa.stat().st_size
    # l'icona viene convertita da CgBI a PNG standard, scegliendo la più grande
    assert info.icon_png and not is_cgbi_png(info.icon_png)
    w, h, _ = decode_plain_png(info.icon_png)
    assert (w, h) == (120, 120)


def test_version_label_without_build(tmp_path):
    info = read_ipa_info(make_ipa(tmp_path / "a.ipa", version="1.0", build="1.0"))
    assert info.version_label == "1.0"


def test_extensions_are_counted(tmp_path):
    assert read_ipa_info(make_ipa(tmp_path / "e.ipa", extensions=2)).extensions == 2


def test_plain_png_icon_is_kept(tmp_path):
    info = read_ipa_info(make_ipa(tmp_path / "p.ipa", icon=True, cgbi=False))
    assert info.icon_png.startswith(PNG_SIGNATURE)
    assert decode_plain_png(info.icon_png)[:2] == (120, 120)


def test_icon_fallback_by_file_name(tmp_path):
    ipa = tmp_path / "f.ipa"
    with zipfile.ZipFile(ipa, "w") as z:
        z.writestr("Payload/X.app/Info.plist", plistlib.dumps({"CFBundleIdentifier": "a.b"}))
        z.writestr("Payload/X.app/Icon-76.png", encode_png(2, 2, 6, b"\xff" * 16))
        z.writestr("Payload/X.app/other.png", encode_png(1, 1, 6, b"\x00" * 4))
    info = read_ipa_info(ipa)
    assert info.icon_png is not None and decode_plain_png(info.icon_png)[:2] == (2, 2)
    assert info.name == "X"  # nessun CFBundleDisplayName/CFBundleName: nome della cartella


def test_cgbi_all_filters_and_alpha(tmp_path):
    w, h = 9, 10
    rgba = bytearray()
    for y in range(h):
        for x in range(w):
            alpha = (255, 200, 128, 64, 0)[(x + y) % 5]
            rgba += bytes([(x * 37) % 256, (y * 53) % 256, (x * y * 7) % 256, alpha])
    data = encode_cgbi(w, h, bytes(rgba), filters=(0, 1, 2, 3, 4))
    assert is_cgbi_png(data)
    png = normalize_png(data)
    assert png and not is_cgbi_png(png)
    dw, dh, px = decode_plain_png(png)
    assert (dw, dh) == (w, h)
    for i in range(0, len(rgba), 4):
        a = rgba[i + 3]
        assert px[i + 3] == a
        if a == 255:
            assert px[i : i + 3] == rgba[i : i + 3]
        elif a > 0:  # premoltiplicazione: piccolo errore di arrotondamento ammesso
            for k in range(3):
                assert abs(px[i + k] - rgba[i + k]) <= 3, (i, k)


def test_normalize_png_rejects_garbage():
    assert normalize_png(b"") is None
    assert normalize_png(b"not a png") is None
    broken = encode_cgbi(4, 4, b"\x10\x20\x30\xff" * 16)
    assert normalize_png(broken[:60]) is None  # IDAT troncato


def test_invalid_files(tmp_path):
    bad = tmp_path / "bad.ipa"
    bad.write_text("ciao")
    with pytest.raises(IpaError, match="non è un .ipa valido"):
        read_ipa_info(bad)

    with pytest.raises(IpaError, match="File non trovato"):
        read_ipa_info(tmp_path / "missing.ipa")

    empty = tmp_path / "empty.ipa"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(IpaError, match="non contiene un'app"):
        read_ipa_info(empty)

    no_id = tmp_path / "noid.ipa"
    with zipfile.ZipFile(no_id, "w") as z:
        z.writestr("Payload/A.app/Info.plist", plistlib.dumps({"CFBundleName": "A"}))
    with pytest.raises(IpaError, match="CFBundleIdentifier"):
        read_ipa_info(no_id)


def test_find_main_app_ignores_extensions():
    names = [
        "Payload/A.app/PlugIns/W.appex/Info.plist",
        "Payload/A.app/Frameworks/F.framework/Info.plist",
        "Payload/A.app/Info.plist",
    ]
    assert find_main_app(names) == ("Payload/A.app", "Payload/A.app/Info.plist")
    assert find_main_app(["Payload/readme"]) is None


def test_signer_parse_bundle_id(tmp_path):
    assert signer.parse_bundle_id_from_ipa(make_ipa(tmp_path / "x.ipa")) == "com.kanga.test"
    junk = tmp_path / "junk.ipa"
    junk.write_bytes(b"zzz")
    assert signer.parse_bundle_id_from_ipa(junk) is None
