"""Download di IPA dalle fonti UFFICIALI dei progetti jailbreak.

Due canali supportati:
  - release GitHub ufficiali (repo 'owner/repo')
  - URL diretto dal sito ufficiale del progetto (es. phoenixpwn.com)

Nessun mirror di terzi, nessun binario modificato.
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from .jailbreak_catalog import JailbreakTool

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


def download_url(url: str, dest: Path, progress_cb=None) -> Path:
    """Scarica un file con progresso e ritorna il percorso locale.

    Per i download diretti dai siti ufficiali aggiunge il referer della stessa
    origine: alcuni siti (es. phoenixpwn.com) bloccano l'hotlinking.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Scarico %s -> %s", url, dest)
    headers = {"User-Agent": "Mozilla/5.0"}
    from urllib.parse import urlparse

    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
    headers["Referer"] = origin
    with requests.get(url, stream=True, timeout=120, headers=headers, allow_redirects=True) as r:
        r.raise_for_status()
        if "html" in (r.headers.get("Content-Type") or "").lower():
            raise DownloadError(
                f"Il server ha risposto con una pagina HTML invece del file "
                f"(l'URL diretto è cambiato?): {r.url}"
            )
        total = int(r.headers.get("Content-Length", 0) or 0)
        done = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(int(done * 100 / total), dest.name)
        tmp.rename(dest)
    if progress_cb:
        progress_cb(100, dest.name)
    return dest


def fetch_tool_ipa(tool: JailbreakTool, dest_dir: Path | None = None, progress_cb=None) -> Path:
    """Scarica l'IPA ufficiale del tool: da GitHub release o URL diretto del sito."""
    dest_dir = dest_dir or DOWNLOAD_DIR
    if tool.repo:
        info = latest_ipa_asset(tool.repo)
        dest = dest_dir / f"{tool.repo.split('/')[0]}-{tool.repo.split('/')[1]}-{info['tag']}-{info['name']}"
        if dest.exists() and dest.stat().st_size == info["size"] and info["size"] > 0:
            logger.info("IPA già presente: %s", dest)
            return dest
        return download_url(info["url"], dest, progress_cb)
    if tool.direct_ipa_url:
        name = tool.direct_ipa_url.rsplit("/", 1)[-1].split("?")[0] or f"{tool.id}.ipa"
        dest = dest_dir / f"{tool.id}-{name}"
        if dest.exists() and dest.stat().st_size > 0:
            logger.info("IPA già presente: %s", dest)
            return dest
        return download_url(tool.direct_ipa_url, dest, progress_cb)
    raise DownloadError(
        f"{tool.name}: nessuna fonte di download automatico — usa il sito ufficiale "
        f"{tool.official_site} e poi 'Installa IPA…'"
    )
