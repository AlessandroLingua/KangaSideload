"""Wrapper del tool zsign per la firma degli IPA.

zsign (MIT, https://github.com/zhlynn/zsign) fa tutto in un comando:
firma di tutti i Mach-O (app principale + frameworks + appex), CodeResources,
entitlements, embedded.mobileprovision, rewrite di bundle id/nome e
ri-packaging in .ipa.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


class ZsignError(RuntimeError):
    pass


def default_zsign_path() -> Path:
    return Path(__file__).resolve().parent.parent / "tools" / "zsign" / "zsign.exe"


def zsign_available(zsign_path: Path | None = None) -> bool:
    p = zsign_path or default_zsign_path()
    return p.exists()


def sign_ipa(
    ipa_in: Path,
    ipa_out: Path,
    p12_path: Path,
    p12_password: str,
    mobileprovision_path: Path,
    entitlements_path: Path,
    bundle_id: str,
    display_name: str | None = None,
    zsign_path: Path | None = None,
    on_output=None,
) -> Path:
    """Firma ipa_in -> ipa_out con certificato p12 + profilo provisioning.

    on_output(str) viene chiamata per ogni riga di output (per il log della GUI).
    """
    zsign = zsign_path or default_zsign_path()
    if not zsign.exists():
        raise ZsignError(
            "zsign.exe non trovato: esegui prima 'python scripts/fetch_zsign.py'"
        )

    cmd = [
        str(zsign),
        "-k", str(p12_path),
        "-p", p12_password,
        "-m", str(mobileprovision_path),
        "-e", str(entitlements_path),
        "-b", bundle_id,
        "-z", "9",
        "-f",
        "-o", str(ipa_out),
    ]
    if display_name:
        cmd += ["-n", display_name]
    cmd.append(str(ipa_in))

    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if on_output:
        for line in output.splitlines():
            on_output(line)
    if proc.returncode != 0:
        raise ZsignError(f"zsign fallito (exit {proc.returncode}): {output[-800:]}")
    if not ipa_out.exists():
        raise ZsignError("zsign è terminato ma il file di output non esiste")
    return ipa_out


def sign_adhoc(ipa_in: Path, ipa_out: Path, zsign_path: Path | None = None) -> Path:
    """Firma ad-hoc (senza certificato): utile per test e validazione IPA."""
    zsign = zsign_path or default_zsign_path()
    if not zsign.exists():
        raise ZsignError("zsign.exe non trovato")
    proc = subprocess.run(
        [str(zsign), "-a", "-z", "9", "-f", "-o", str(ipa_out), str(ipa_in)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise ZsignError(f"zsign ad-hoc fallito: {(proc.stdout or '') + (proc.stderr or '')[-600:]}")
    return ipa_out


def parse_bundle_id_from_ipa(ipa_path: Path) -> str | None:
    """Legge CFBundleIdentifier dall'Info.plist principale dell'IPA."""
    import plistlib
    import zipfile

    try:
        with zipfile.ZipFile(ipa_path) as z:
            for n in z.namelist():
                if n.startswith("Payload/") and n.endswith(".app/Info.plist") and n.count("/") == 2:
                    with z.open(n) as f:
                        pl = plistlib.load(f)
                    return pl.get("CFBundleIdentifier")
    except Exception:
        return None
    return None


def readable_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
