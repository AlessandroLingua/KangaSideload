"""Crea sul Desktop il collegamento «Kanga Sideload» con l'icona dell'app.

Il collegamento avvia `.venv\\Scripts\\pythonw.exe -m kanga` dalla cartella del
progetto, senza finestra del terminale. Se sposti la cartella, rilancia lo script.

Uso:  .venv\\Scripts\\python.exe scripts\\crea_collegamento.py [cartella di destinazione]
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NAME = "Kanga Sideload"
SIZES = (16, 24, 32, 48, 64, 128, 256)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def build_ico(dest: Path) -> Path:
    """Icona .ico multi-risoluzione (voci PNG) disegnata come il logo dell'app.

    Usa la piattaforma grafica normale di Windows (non apre finestre): quella
    "offscreen" non ha font e la «K» verrebbe disegnata come un quadratino.
    """
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    from kanga.gui import icons

    _app = QGuiApplication.instance() or QGuiApplication([])
    images = []
    for size in SIZES:
        px = icons.letter_tile("K", size, "#3B82F6", "#1E3A8A", dpr=1.0)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        px.save(buf, "PNG")
        images.append((size, bytes(buf.data())))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # nel formato ICO 0 significa 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + data)
    return dest


def _powershell(script: str, **env) -> str:
    """Esegue PowerShell passando i valori come variabili d'ambiente (niente problemi con gli spazi)."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, **env}, capture_output=True, text=True, check=True, creationflags=NO_WINDOW,
    )
    return proc.stdout.strip()


def desktop_dir() -> Path:
    return Path(_powershell("[Environment]::GetFolderPath('Desktop')"))


def create_shortcut(lnk: Path, target: Path, args: str, workdir: Path, icon: Path) -> Path:
    _powershell(
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:K_LNK);"
        "$s.TargetPath = $env:K_TARGET; $s.Arguments = $env:K_ARGS;"
        "$s.WorkingDirectory = $env:K_DIR; $s.IconLocation = $env:K_ICON + ',0';"
        "$s.Description = 'Kanga Sideload: sideload, jailbreak e firmware iOS'; $s.Save()",
        K_LNK=str(lnk), K_TARGET=str(target), K_ARGS=args, K_DIR=str(workdir), K_ICON=str(icon),
    )
    return lnk


def main() -> int:
    if os.name != "nt":
        print("Il collegamento si crea solo su Windows.")
        return 1
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        print(f"Manca {pythonw}: crea prima l'ambiente (vedi README, «Installazione»).")
        return 1
    from kanga.core.store import base_dir

    (base_dir() / "kanga.ico").unlink(missing_ok=True)  # prima versione, con la «K» illeggibile
    icon = build_ico(base_dir() / "kanga-sideload.ico")
    dest_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else desktop_dir()
    lnk = create_shortcut(dest_dir / f"{NAME}.lnk", pythonw, "-m kanga", ROOT, icon)
    print(f"Collegamento creato: {lnk}")
    print(f"Icona: {icon}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
