"""Punto d'ingresso: python -m kanga"""
from __future__ import annotations

import logging
import os
import sys


def _windows_app_id() -> None:
    """Su Windows fa comparire l'icona di Kanga (non quella di Python) nella barra applicazioni."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kanga.Sideload")
    except Exception:
        pass


def _setup_logging() -> None:
    """Console (se c'è) + file %LOCALAPPDATA%/Kanga/kanga.log: avviata con doppio clic
    (pythonw) l'app non ha console, e senza file un errore all'avvio sparirebbe."""
    from logging.handlers import RotatingFileHandler

    from .core.store import base_dir

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        base_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(base_dir() / "kanga.log", maxBytes=1_000_000, backupCount=2,
                                            encoding="utf-8"))
    except OSError:
        pass
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers or [logging.NullHandler()])

    def excepthook(exc_type, exc, tb):
        logging.getLogger("kanga").critical("Errore non gestito", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook


def main() -> int:
    _setup_logging()
    from PySide6.QtWidgets import QApplication

    from . import __version__
    from .gui import icons, theme, workers
    from .gui.main_window import MainWindow

    _windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Kanga Sideload")
    app.setOrganizationName("Kanga")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(icons.app_icon())
    theme.apply_theme(app)

    win = MainWindow()
    win.show()
    rc = app.exec()
    if workers.any_running():
        # un'operazione (es. installazione) è stata interrotta chiudendo la finestra:
        # i thread di lavoro non si possono fermare in sicurezza, si esce subito
        os._exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
