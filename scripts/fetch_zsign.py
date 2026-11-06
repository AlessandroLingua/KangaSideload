"""Scarica zsign per Windows x64 dalle release GitHub ufficiali (zhlynn/zsign, MIT).

Uso: python scripts/fetch_zsign.py
Estrae zsign.exe (e dipendenze) in kanga/tools/zsign/
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import requests

REPO = "zhlynn/zsign"
# fallback fisso se l'API non risponde
FALLBACK_TAG = "v1.1.3"
DEST = Path(__file__).resolve().parent.parent / "kanga" / "tools" / "zsign"


def latest_release():
    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("tag_name"), data.get("assets", [])
    except Exception as e:
        print(f"[!] API GitHub non disponibile ({e}), uso fallback {FALLBACK_TAG}")
        return FALLBACK_TAG, [
            {
                "name": "zsign-windows-x64.zip",
                "browser_download_url": f"https://github.com/{REPO}/releases/download/{FALLBACK_TAG}/zsign-windows-x64.zip",
            }
        ]


def main():
    tag, assets = latest_release()
    win_assets = [a for a in assets if "windows-x64" in a["name"].lower()]
    if not win_assets:
        print(f"[!] Nessun asset windows-x64 nella release {tag}")
        sys.exit(1)
    url = win_assets[0]["browser_download_url"]
    print(f"[*] Scarico {url} (release {tag})...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()

    DEST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(DEST)

    exe = DEST / "zsign.exe"
    if not exe.exists():
        # a volte l'exe è in una sottocartella
        candidates = list(DEST.rglob("zsign.exe"))
        if candidates:
            candidates[0].rename(exe)
    if not exe.exists():
        print("[!] zsign.exe non trovato nell'archivio")
        sys.exit(1)
    print(f"[+] zsign installato: {exe}")
    print(f"[+] versione: ", end="")
    import subprocess

    out = subprocess.run([str(exe)], capture_output=True, text=True)
    print((out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else "?")
    # salva la versione per controlli futuri
    (DEST / "VERSION.txt").write_text(tag, encoding="utf-8")


if __name__ == "__main__":
    main()
