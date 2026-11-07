"""Download di IPA dalle fonti UFFICIALI dei progetti jailbreak.

Due canali supportati:
  - release GitHub ufficiali (repo 'owner/repo')
  - URL diretto dal sito ufficiale del progetto (es. phoenixpwn.com)

Nessun mirror di terzi, nessun binario modificato.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import store
from .jailbreak_catalog import JailbreakTool

logger = logging.getLogger(__name__)

API = "https://api.github.com/repos/{repo}/releases/latest"


def download_dir() -> Path:
    """Cache degli IPA scaricati: %LOCALAPPDATA%/Kanga/downloads (fuori dal progetto)."""
    return store.downloads_dir()


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


def download_url(url: str, dest: Path, progress_cb=None, expected_size: int = 0) -> Path:
    """Scarica un file con progresso e ritorna il percorso locale.

    Per i download diretti dai siti ufficiali aggiunge il referer della stessa
    origine: alcuni siti (es. phoenixpwn.com) bloccano l'hotlinking. Un file
    incompleto o che non è un IPA viene scartato e riscaricato una volta.
    """
    last_error: DownloadError | None = None
    for attempt in (1, 2):
        try:
            return _download_once(url, dest, progress_cb, expected_size)
        except DownloadError as e:
            last_error = e
            logger.warning("Download non valido (tentativo %d): %s", attempt, e)
    raise last_error


def _download_once(url: str, dest: Path, progress_cb, expected_size: int) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Scarico %s -> %s", url, dest)
    parsed = urlparse(url)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": f"{parsed.scheme}://{parsed.netloc}/"}
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=120, headers=headers, allow_redirects=True) as r:
            r.raise_for_status()
            content_type = r.headers.get("Content-Type") or "?"
            if "html" in content_type.lower():
                raise DownloadError(
                    f"Il server ha risposto con una pagina HTML invece del file "
                    f"(l'URL diretto è cambiato?): {r.url}"
                )
            total = int(r.headers.get("Content-Length", 0) or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb and total:
                            progress_cb(int(done * 100 / total), dest.name)
        expected = expected_size or total
        if expected and done != expected:
            raise DownloadError(f"Download incompleto da {url}: {done} byte su {expected}")
        if not zipfile.is_zipfile(tmp):
            head = tmp.read_bytes()[:60].decode("latin-1", "replace")
            raise DownloadError(
                f"Il file scaricato da {url} non è un IPA valido "
                f"({done} byte, tipo {content_type}, inizio: {head!r})"
            )
        tmp.replace(dest)  # replace: su Windows rename fallisce se dest esiste già
    finally:
        tmp.unlink(missing_ok=True)
    if progress_cb:
        progress_cb(100, dest.name)
    return dest


def _direct_dest_name(tool: JailbreakTool) -> str:
    """Nome locale per un download diretto: sempre con estensione .ipa
    (es. phoenixpwn.com/download.php -> phoenix.ipa)."""
    name = tool.direct_ipa_url.rsplit("/", 1)[-1].split("?")[0]
    return f"{tool.id}-{name}" if name.lower().endswith(".ipa") else f"{tool.id}.ipa"


def fetch_tool_ipa(tool: JailbreakTool, dest_dir: Path | None = None, progress_cb=None) -> Path:
    """Scarica l'IPA ufficiale del tool: da GitHub release o URL diretto del sito."""
    dest_dir = Path(dest_dir) if dest_dir else download_dir()
    if tool.repo:
        info = latest_ipa_asset(tool.repo)
        owner, repo = tool.repo.split("/", 1)
        dest = dest_dir / f"{owner}-{repo}-{info['tag']}-{info['name']}"
        if dest.exists() and dest.stat().st_size == info["size"] and info["size"] > 0:
            logger.info("IPA già presente: %s", dest)
            return dest
        return download_url(info["url"], dest, progress_cb, expected_size=info["size"])
    if tool.direct_ipa_url:
        dest = dest_dir / _direct_dest_name(tool)
        if dest.exists() and zipfile.is_zipfile(dest):
            logger.info("IPA già presente: %s", dest)
            return dest
        return download_url(tool.direct_ipa_url, dest, progress_cb)
    raise DownloadError(
        f"{tool.name}: nessuna fonte di download automatico — usa il sito ufficiale "
        f"{tool.official_site} e poi 'Installa IPA…'"
    )
