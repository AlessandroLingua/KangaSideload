"""Test della pipeline di firma: genera un IPA di test con un Mach-O ARM64
minimale valido e verifica che zsign lo firmi (ad-hoc) producendo un IPA
con CodeResources e signature blob.
"""
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanga.core import signer  # noqa: E402
from kanga.core.entitlements import free_provisioning_entitlements  # noqa: E402

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

    text_cmdsize = 72 + 80  # segment_command_64 + una section_64
    linkedit_cmdsize = 72  # segment_command_64 senza sezioni
    main_cmdsize = 24
    header_size = 32
    ncmds = 3
    sizeofcmds = text_cmdsize + linkedit_cmdsize + main_cmdsize
    header_pad = 128  # spazio per LC_CODE_SIGNATURE inserito da zsign
    linkedit_off = 12 * 1024

    header = struct.pack(
        "<IIIIIIiI",
        MH_MAGIC_64,
        CPU_TYPE_ARM64,
        0,
        MH_EXECUTE,
        ncmds,
        sizeofcmds,
        MH_NOUNDEFS | MH_TWOLEVEL | MH_PIE,
        0,
    )

    seg = struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64,
        text_cmdsize,
        b"__TEXT",
        0,  # vmaddr
        linkedit_off,  # vmsize
        0,  # fileoff
        linkedit_off,  # filesize
        0x5, 0x5,  # maxprot, initprot (r-x)
        1, 0,  # nsects, flags
    )
    text_start = header_size + sizeofcmds + header_pad
    sect = struct.pack(
        "<16s16sQQIIiiIIII",
        b"__text",
        b"__TEXT",
        text_start,  # addr
        linkedit_off - text_start,  # size
        text_start,  # offset nel file
        4,  # align 2^4
        0, 0, 0, 0, 0, 0,
    )

    main_lc = struct.pack("<IIQQ", LC_MAIN, main_cmdsize, text_start, 0)

    linkedit = struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64,
        linkedit_cmdsize,
        b"__LINKEDIT",
        linkedit_off,  # vmaddr
        file_size - linkedit_off,  # vmsize
        linkedit_off,  # fileoff
        file_size - linkedit_off,  # filesize
        0x1, 0x1,  # maxprot, initprot (r--)
        0, 0,  # nsects, flags
    )

    body = header + seg + sect + main_lc + linkedit
    out = bytearray(file_size)
    out[: len(body)] = body
    return bytes(out)


def build_test_ipa(dest: Path) -> Path:
    macho = build_minimal_macho()
    info_plist = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>KangaTest</string>
<key>CFBundleIdentifier</key><string>com.kanga.test</string>
<key>CFBundleName</key><string>KangaTest</string>
<key>CFBundleDisplayName</key><string>Kanga Test</string>
<key>CFBundleVersion</key><string>1.0</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>MinimumOSVersion</key><string>14.0</string>
<key>CFBundleSupportedPlatforms</key><array><string>iPhoneOS</string></array>
<key>DTSDKName</key><string>iphoneos17.0</string>
</dict></plist>"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Payload/KangaTest.app/KangaTest", macho)
        z.writestr("Payload/KangaTest.app/Info.plist", info_plist)
        z.writestr("Payload/KangaTest.app/PkgInfo", b"APPL????")
    return dest


def test_bundle_id_parse(tmp: Path):
    ipa = build_test_ipa(tmp / "test.ipa")
    assert signer.parse_bundle_id_from_ipa(ipa) == "com.kanga.test"
    print("[OK] parse_bundle_id_from_ipa")


def test_adhoc_sign(tmp: Path):
    ipa = build_test_ipa(tmp / "test.ipa")
    out = tmp / "test_signed.ipa"
    signer.sign_adhoc(ipa, out)
    assert out.exists() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert any("Payload/KangaTest.app/_CodeSignature/CodeResources" in n for n in names), names
        # il Mach-O deve contenere ora un signature blob (magic 0xfade0cc0)
        macho_name = next(n for n in names if n.endswith("Payload/KangaTest.app/KangaTest"))
        data = z.read(macho_name)
        assert b"\xfa\xde\x0c\xc0" in data, "signature blob mancante nel Mach-O"
    print(f"[OK] firma ad-hoc: {signer.readable_size(out.stat().st_size)}, CodeResources + blob presenti")


def test_entitlements(tmp: Path):
    ents_b = free_provisioning_entitlements("ABC123", "com.kanga.app")
    import plistlib

    ents = plistlib.loads(ents_b)
    assert ents["application-identifier"] == "ABC123.com.kanga.app"
    assert ents["get-task-allow"] is True

    stripped = free_provisioning_entitlements_strip_test()
    assert "com.apple.security.application-groups" not in stripped
    assert "com.apple.security.network.client" in stripped
    print("[OK] entitlements generate + strip per account free")


def free_provisioning_entitlements_strip_test():
    from kanga.core.entitlements import strip_entitlements_for_free_account

    fake_app_ents = {
        "application-identifier": "OLD.oldid",
        "com.apple.security.application-groups": ["group.x"],
        "com.apple.developer.icloud-services": ["CloudDocuments"],
        "com.apple.security.network.client": True,
        "aps-environment": "development",
    }
    return strip_entitlements_for_free_account(fake_app_ents)


if __name__ == "__main__":
    tmp = Path(__file__).resolve().parent / "test_output"
    test_bundle_id_parse(tmp)
    test_adhoc_sign(tmp)
    test_entitlements(tmp)
    print("\nTutti i test firma: PASSATI")
