"""Download di zsign dalle release GitHub ufficiali (zhlynn/zsign, MIT).

Usato sia dallo script `scripts/fetch_zsign.py` sia dal pulsante
«Scarica zsign» della GUI.
"""
from __future__ import annotations

import io
import os
import platform
import stat
import subprocess
import zipfile
from pathlib import Path

from .signer import _exe_name, _no_window_flags, tools_dir

REPO = "zhlynn/zsign"
FALLBACK_TAG = "v1.1.3"  # usato se l'API GitHub non risponde


class ZsignFetchError(RuntimeError):
    pass


def platform_keywords(system: str | None = None) -> tuple[str, ...]:
    """Parole chiave per riconoscere l'asset giusto per questo sistema."""
    system = (system or platform.system()).lower()
    if system == "windows":
        return ("windows-x64", "win64", "windows")
    if system == "darwin":
        return ("macos", "darwin", "osx")
    return ("linux-x64", "linux")


def pick_asset(assets: list[dict], system: str | None = None) -> dict | None:
    """Sceglie l'asset .zip più adatto alla piattaforma (in ordine di preferenza)."""
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    for kw in platform_keywords(system):
        for a in zips:
            if kw in a["name"].lower():
                return a
    return None


def latest_release(log=print) -> tuple[str, list[dict]]:
    import requests

    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("tag_name") or FALLBACK_TAG, data.get("assets", [])
    except Exception as e:
        log(f"API GitHub non disponibile ({e}), uso la release {FALLBACK_TAG}")
        return FALLBACK_TAG, [
            {
                "name": "zsign-windows-x64.zip",
                "browser_download_url": (
                    f"https://github.com/{REPO}/releases/download/{FALLBACK_TAG}/zsign-windows-x64.zip"
                ),
            }
        ]


def fetch_zsign(log=print, dest: Path | None = None) -> Path:
    """Scarica ed estrae zsign. Ritorna il percorso dell'eseguibile."""
    import requests

    dest = Path(dest or tools_dir())
    tag, assets = latest_release(log)
    asset = pick_asset(assets)
    if asset is None:
        raise ZsignFetchError(
            f"Nessun pacchetto zsign per {platform.system()} nella release {tag}"
        )
    url = asset["browser_download_url"]
    log(f"Scarico {asset['name']} (release {tag})…")
    r = requests.get(url, timeout=120)
    r.raise_for_status()

    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(dest)

    exe = dest / _exe_name()
    if not exe.exists():
        candidates = list(dest.rglob(_exe_name()))
        if candidates:
            candidates[0].replace(exe)
    if not exe.exists():
        raise ZsignFetchError(f"{_exe_name()} non trovato nell'archivio scaricato")
    if os.name != "nt":
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (dest / "VERSION.txt").write_text(tag, encoding="utf-8")
    try:
        out = subprocess.run(
            [str(exe)], capture_output=True, text=True, timeout=15,
            creationflags=_no_window_flags(),
        )
        first = (out.stdout or out.stderr or "?").splitlines()
        log(f"zsign installato: {exe} ({first[0] if first else '?'})")
    except Exception:
        log(f"zsign installato: {exe}")
    return exe
