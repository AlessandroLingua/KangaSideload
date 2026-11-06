"""Firma IPA: wrapper zsign, entitlements, ricerca/download di zsign.

I test con lo zsign reale (firma ad-hoc) girano solo se zsign è disponibile
(kanga/tools/zsign, PATH o variabile KANGA_ZSIGN); gli altri usano uno zsign
finto per verificare il wrapper.
"""
import os
import plistlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fakes import make_ipa  # noqa: E402
from kanga.core import signer  # noqa: E402
from kanga.core.entitlements import (  # noqa: E402
    free_provisioning_entitlements,
    strip_entitlements_for_free_account,
)
from kanga.core.zsign_fetch import pick_asset, platform_keywords  # noqa: E402

needs_zsign = pytest.mark.skipif(not signer.zsign_available(), reason="zsign non installato")
posix_only = pytest.mark.skipif(os.name == "nt", reason="zsign finto come script POSIX")


@needs_zsign
def test_adhoc_sign_real_zsign(tmp_path):
    ipa = make_ipa(tmp_path / "test.ipa")
    out = signer.sign_adhoc(ipa, tmp_path / "test_signed.ipa")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert any(n.endswith("Payload/KangaTest.app/_CodeSignature/CodeResources") for n in names)
        data = z.read(next(n for n in names if n.endswith("Payload/KangaTest.app/KangaTest")))
        assert b"\xfa\xde\x0c\xc0" in data, "signature blob mancante nel Mach-O"


@pytest.fixture
def fake_zsign(tmp_path, monkeypatch):
    """Uno 'zsign' finto: registra gli argomenti, stampa righe di log, scrive l'output."""
    script = tmp_path / "zsign"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, os\n"
        "args = sys.argv[1:]\n"
        "open(os.environ['FAKE_ZSIGN_LOG'], 'w').write(json.dumps(args))\n"
        "print('>>> Signing: KangaTest')\n"
        "print('>>> Done.')\n"
        "if os.environ.get('FAKE_ZSIGN_FAIL'):\n"
        "    print('error: bad certificate', file=sys.stderr); sys.exit(3)\n"
        "open(args[args.index('-o') + 1], 'wb').write(b'signed')\n"
    )
    script.chmod(0o755)
    log = tmp_path / "args.json"
    monkeypatch.setenv("KANGA_ZSIGN", str(script))
    monkeypatch.setenv("FAKE_ZSIGN_LOG", str(log))
    return log


@posix_only
def test_sign_ipa_command_and_streaming(tmp_path, fake_zsign):
    import json

    lines = []
    out = signer.sign_ipa(
        ipa_in=tmp_path / "in.ipa", ipa_out=tmp_path / "out.ipa", p12_path=tmp_path / "c.p12",
        p12_password="pw", mobileprovision_path=tmp_path / "p.mobileprovision",
        entitlements_path=tmp_path / "e.plist", bundle_id="com.x.y", display_name="Mio Nome",
        on_output=lines.append,
    )
    assert out.read_bytes() == b"signed"
    args = json.loads(fake_zsign.read_text())
    assert args[args.index("-b") + 1] == "com.x.y"
    assert args[args.index("-n") + 1] == "Mio Nome"
    assert args[args.index("-p") + 1] == "pw"
    assert args[-1] == str(tmp_path / "in.ipa")
    assert lines == [">>> Signing: KangaTest", ">>> Done."]


@posix_only
def test_sign_ipa_failure_reports_output(tmp_path, fake_zsign, monkeypatch):
    monkeypatch.setenv("FAKE_ZSIGN_FAIL", "1")
    with pytest.raises(signer.ZsignError, match=r"(?s)exit 3.*bad certificate"):
        signer.sign_ipa(tmp_path / "i.ipa", tmp_path / "o.ipa", tmp_path / "c.p12", "pw",
                        tmp_path / "p", tmp_path / "e", "com.x")


def test_missing_zsign_has_actionable_message(tmp_path, monkeypatch):
    monkeypatch.delenv("KANGA_ZSIGN", raising=False)
    monkeypatch.setattr(signer, "find_zsign", lambda: None)
    assert not signer.zsign_available()
    with pytest.raises(signer.ZsignError, match="fetch_zsign"):
        signer.sign_adhoc(tmp_path / "a.ipa", tmp_path / "b.ipa")


def test_zsign_lookup_order(tmp_path, monkeypatch):
    exe = tmp_path / "my-zsign"
    exe.write_text("x")
    monkeypatch.setenv("KANGA_ZSIGN", str(exe))
    assert signer.find_zsign() == exe
    assert signer.zsign_available(exe) and not signer.zsign_available(tmp_path / "nope")
    assert signer.default_zsign_path().name in ("zsign", "zsign.exe")


def test_pick_zsign_asset():
    assets = [
        {"name": "zsign-v1.2-linux-x64.zip"},
        {"name": "zsign-v1.2-macos-universal.zip"},
        {"name": "zsign-v1.2-windows-x64.zip"},
        {"name": "zsign-src.tar.gz"},
    ]
    assert pick_asset(assets, "Windows")["name"].endswith("windows-x64.zip")
    assert pick_asset(assets, "Linux")["name"].endswith("linux-x64.zip")
    assert pick_asset(assets, "Darwin")["name"].endswith("macos-universal.zip")
    assert pick_asset([{"name": "zsign.tar.gz"}], "Linux") is None
    assert platform_keywords("windows")[0] == "windows-x64"


def test_entitlements_for_free_account():
    ents = plistlib.loads(free_provisioning_entitlements("ABC123", "com.kanga.app"))
    assert ents["application-identifier"] == "ABC123.com.kanga.app"
    assert ents["com.apple.developer.team-identifier"] == "ABC123"
    assert ents["get-task-allow"] is True
    assert ents["keychain-access-groups"] == ["ABC123.com.kanga.app"]

    stripped = strip_entitlements_for_free_account({
        "application-identifier": "OLD.oldid",
        "com.apple.security.application-groups": ["group.x"],
        "com.apple.developer.icloud-services": ["CloudDocuments"],
        "com.apple.developer.networking.wifi-info": True,
        "com.apple.security.network.client": True,
        "aps-environment": "development",
    })
    assert "com.apple.security.application-groups" not in stripped
    assert "com.apple.developer.icloud-services" not in stripped
    assert stripped["com.apple.developer.networking.wifi-info"] is True  # preservato
    assert stripped["com.apple.security.network.client"] is True


def test_readable_size():
    assert signer.readable_size(512) == "512 B"
    assert signer.readable_size(2048) == "2.0 KB"
    assert signer.readable_size(5 * 1024 * 1024) == "5.0 MB"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
