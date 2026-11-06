"""Download di IPA dalle release GitHub UFFICIALI dei progetti jailbreak.

Usa solo l'API GitHub pubblica degli owner repository citati nel catalogo.
Nessun mirror di terzi, nessun binario modificato.
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

API = "https://api.github.com/repos/{repo}/releases/latest"
DOWNLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "downloads"


class DownloadError(RuntimeError):
    pass


def latest_ipa_asset(repo: str) -> dict:
    """Info sull'asset .ipa dell'ultima release: {tag, name, url, size}."""
    url = API.format(repo=repo)
    r = requests.get(url, timeout=20, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    data = r.json()
    if not data.get("assets"):
        raise DownloadError(f"Nessun asset nella release {data.get('tag_name', '?')} di {repo}")
    ipa_assets = [a for a in data["assets"] if a["name"].lower().endswith(".ipa")]
    if not ipa_assets:
        names = ", ".join(a["name"] for a in data["assets"])
        raise DownloadError(f"Nessun .ipa nella release di {repo} (asset: {names})")
    a = ipa_assets[0]
    return {
        "tag": data.get("tag_name", "?"),
        "name": a["name"],
        "url": a["browser_download_url"],
        "size": a.get("size", 0),
        "release_notes": (data.get("body") or "")[:400],
    }


def download_ipa(repo: str, dest_dir: Path | None = None, progress_cb=None) -> Path:
    """Scarica l'IPA dell'ultima release ufficiale e ritorna il percorso locale."""
    info = latest_ipa_asset(repo)
    dest_dir = dest_dir or DOWNLOAD_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{repo.split('/')[0]}-{repo.split('/')[1]}-{info['tag']}-{info['name']}"

    if dest.exists() and dest.stat().st_size == info["size"] and info["size"] > 0:
        logger.info("IPA già presente: %s", dest)
        return dest

    logger.info("Scarico %s (%s) da %s", info["name"], info["tag"], info["url"])
    with requests.get(info["url"], stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", info["size"]) or 0)
        done = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(int(done * 100 / total), info["name"])
        tmp.rename(dest)
    if progress_cb:
        progress_cb(100, info["name"])
    return dest
