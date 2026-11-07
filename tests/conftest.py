"""Configurazione comune dei test: Qt senza display e dati in una cartella temporanea."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    """Ogni test usa una %LOCALAPPDATA% tutta sua: niente sessioni reali toccate."""
    d = tmp_path / "appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(d))
    return d / "Kanga"


@pytest.fixture
def themed_app(qapp):
    from kanga.gui import theme

    theme.apply_theme(qapp, dark=False)
    return qapp
