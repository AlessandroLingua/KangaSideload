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


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
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
