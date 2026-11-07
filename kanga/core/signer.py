"""Wrapper del tool zsign per la firma degli IPA.

zsign (MIT, https://github.com/zhlynn/zsign) fa tutto in un comando:
firma di tutti i Mach-O (app principale + frameworks + appex), CodeResources,
entitlements, embedded.mobileprovision, rewrite di bundle id/nome e
ri-packaging in .ipa.

Ricerca del binario (in ordine): variabile d'ambiente KANGA_ZSIGN,
kanga/tools/zsign/ (scaricato da scripts/fetch_zsign.py), PATH di sistema.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .ipa import find_main_app


class ZsignError(RuntimeError):
    pass


def _exe_name() -> str:
    return "zsign.exe" if os.name == "nt" else "zsign"


def tools_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tools" / "zsign"


def default_zsign_path() -> Path:
    return tools_dir() / _exe_name()


def find_zsign() -> Path | None:
    env = os.environ.get("KANGA_ZSIGN")
    if env and Path(env).is_file():
        return Path(env)
    bundled = default_zsign_path()
    if bundled.is_file():
        return bundled
    found = shutil.which("zsign")
    return Path(found) if found else None


def zsign_available(zsign_path: Path | None = None) -> bool:
    if zsign_path is not None:
        return Path(zsign_path).is_file()
    return find_zsign() is not None


def _resolve(zsign_path: Path | None) -> Path:
    zsign = Path(zsign_path) if zsign_path else find_zsign()
    if zsign is None or not zsign.is_file():
        raise ZsignError(
            "zsign non trovato: scaricalo da Kanga (barra in basso) oppure esegui "
            "'python scripts/fetch_zsign.py'"
        )
    return zsign


def _no_window_flags() -> int:
    # evita il lampeggio di una finestra console quando la GUI lancia zsign su Windows
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(cmd: list[str], on_output=None) -> tuple[int, str]:
    """Esegue zsign inoltrando l'output riga per riga (log in tempo reale)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_no_window_flags(),
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        lines.append(line)
        if on_output:
            on_output(line)
    return proc.wait(), "\n".join(lines)


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
    zsign = _resolve(zsign_path)
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

    code, output = _run(cmd, on_output)
    if code != 0:
        raise ZsignError(f"zsign fallito (exit {code}): {output[-800:]}")
    if not Path(ipa_out).exists():
        raise ZsignError("zsign è terminato ma il file di output non esiste")
    return Path(ipa_out)


def sign_adhoc(ipa_in: Path, ipa_out: Path, zsign_path: Path | None = None) -> Path:
    """Firma ad-hoc (senza certificato): utile per test e validazione IPA."""
    zsign = _resolve(zsign_path)
    code, output = _run([str(zsign), "-a", "-z", "9", "-f", "-o", str(ipa_out), str(ipa_in)])
    if code != 0:
        raise ZsignError(f"zsign ad-hoc fallito: {output[-600:]}")
    return Path(ipa_out)


def parse_bundle_id_from_ipa(ipa_path: Path) -> str | None:
    """Legge CFBundleIdentifier dall'Info.plist principale dell'IPA."""
    import plistlib
    import zipfile

    try:
        with zipfile.ZipFile(ipa_path) as z:
            found = find_main_app(z.namelist())
            if not found:
                return None
            pl = plistlib.loads(z.read(found[1]))
            return pl.get("CFBundleIdentifier")
    except Exception:
        return None


def readable_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
