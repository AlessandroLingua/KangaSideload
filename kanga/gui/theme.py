"""Tema grafico: palette chiara/scura (segue il sistema) + foglio di stile Qt.

I colori sono token: i widget disegnati a mano (step, badge, avatar) li
leggono da `current()`, il resto passa dal foglio di stile.
Forza un tema con la variabile d'ambiente KANGA_THEME=light|dark.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True)
class Tokens:
    dark: bool
    window: str
    surface: str        # card
    surface_alt: str    # zone secondarie (drop zone, log)
    border: str
    border_strong: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    on_accent: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    input_bg: str


LIGHT = Tokens(
    dark=False,
    window="#F3F4F6",
    surface="#FFFFFF",
    surface_alt="#F8FAFC",
    border="#E3E6EB",
    border_strong="#CBD2DB",
    text="#161A1F",
    muted="#5B6573",
    accent="#2563EB",
    accent_hover="#1D4ED8",
    accent_pressed="#1E40AF",
    accent_soft="#E8EFFD",
    on_accent="#FFFFFF",
    success="#15803D",
    success_soft="#E6F5EC",
    warning="#B45309",
    warning_soft="#FDF3E2",
    danger="#B91C1C",
    danger_soft="#FCEBEB",
    input_bg="#FFFFFF",
)

DARK = Tokens(
    dark=True,
    window="#131518",
    surface="#1C1F23",
    surface_alt="#171A1D",
    border="#2C3138",
    border_strong="#3C434C",
    text="#E9ECEF",
    muted="#9AA4B1",
    accent="#3B82F6",
    accent_hover="#5B95F7",
    accent_pressed="#2563EB",
    accent_soft="#1A2A47",
    on_accent="#FFFFFF",
    success="#4ADE80",
    success_soft="#14291C",
    warning="#FBBF24",
    warning_soft="#2E2410",
    danger="#F87171",
    danger_soft="#341A1A",
    input_bg="#15171A",
)

_current: Tokens = LIGHT


def current() -> Tokens:
    return _current


def system_prefers_dark() -> bool:
    forced = os.environ.get("KANGA_THEME", "").lower()
    if forced in ("dark", "light"):
        return forced == "dark"
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:  # Qt < 6.5
        pass
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _palette(t: Tokens) -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.ColorRole.Window, c(t.window))
    p.setColor(QPalette.ColorRole.WindowText, c(t.text))
    p.setColor(QPalette.ColorRole.Base, c(t.input_bg))
    p.setColor(QPalette.ColorRole.AlternateBase, c(t.surface_alt))
    p.setColor(QPalette.ColorRole.Text, c(t.text))
    p.setColor(QPalette.ColorRole.Button, c(t.surface))
    p.setColor(QPalette.ColorRole.ButtonText, c(t.text))
    p.setColor(QPalette.ColorRole.Highlight, c(t.accent))
    p.setColor(QPalette.ColorRole.HighlightedText, c(t.on_accent))
    p.setColor(QPalette.ColorRole.ToolTipBase, c(t.surface))
    p.setColor(QPalette.ColorRole.ToolTipText, c(t.text))
    p.setColor(QPalette.ColorRole.PlaceholderText, c(t.muted))
    p.setColor(QPalette.ColorRole.Link, c(t.accent))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, c(t.muted))
    return p


def stylesheet(t: Tokens) -> str:
    return f"""
    QWidget {{ color: {t.text}; }}
    QMainWindow, QDialog, QWidget#Page, QScrollArea, QWidget#ScrollContent {{
        background: {t.window};
    }}
    QToolTip {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border_strong}; border-radius: 6px; padding: 4px 8px;
    }}

    /* ---- header ---- */
    QFrame#Header {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
    QLabel#AppTitle {{ font-size: 16px; font-weight: 700; }}
    QLabel#AppSubtitle {{ color: {t.muted}; font-size: 12px; }}

    /* ---- navigazione a segmenti ---- */
    QFrame#NavBar {{
        background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 10px;
    }}
    QPushButton[nav="true"] {{
        background: transparent; border: none; border-radius: 7px;
        padding: 6px 14px; color: {t.muted}; font-weight: 600;
    }}
    QPushButton[nav="true"]:hover {{ color: {t.text}; }}
    QPushButton[nav="true"]:checked {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    }}

    /* ---- card ---- */
    QFrame[card="true"] {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px;
    }}
    QLabel[role="cardTitle"] {{ font-size: 14px; font-weight: 700; }}
    QLabel[role="h2"] {{ font-size: 18px; font-weight: 700; }}
    QLabel[role="title"] {{ font-size: 14px; font-weight: 600; }}
    QLabel[role="muted"] {{ color: {t.muted}; }}
    QLabel[role="small"] {{ color: {t.muted}; font-size: 11px; }}
    QLabel[role="error"] {{ color: {t.danger}; }}
    QLabel[role="stepBadge"] {{
        background: {t.accent_soft}; color: {t.accent}; border-radius: 11px;
        font-weight: 700; font-size: 12px;
    }}
    QLabel[role="stepBadge"][done="true"] {{ background: {t.success_soft}; color: {t.success}; }}

    /* ---- pulsanti ---- */
    QPushButton {{
        background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 8px;
        padding: 7px 14px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {t.surface_alt}; border-color: {t.muted}; }}
    QPushButton:pressed {{ background: {t.border}; }}
    QPushButton:disabled {{ color: {t.muted}; background: {t.surface_alt}; border-color: {t.border}; }}
    QPushButton:focus {{ border-color: {t.accent}; }}
    QPushButton[variant="primary"] {{
        background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent};
    }}
    QPushButton[variant="primary"]:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
    QPushButton[variant="primary"]:pressed {{ background: {t.accent_pressed}; }}
    QPushButton[variant="primary"]:disabled {{
        background: {t.border}; color: {t.muted}; border-color: {t.border};
    }}
    QPushButton[variant="primary"]:focus {{ border: 2px solid {t.accent_pressed}; }}
    QPushButton[variant="big"] {{ padding: 10px 22px; font-size: 14px; }}
    QPushButton[variant="link"], QPushButton[variant="ghost"] {{
        background: transparent; border: none; color: {t.accent}; padding: 4px 6px;
    }}
    QPushButton[variant="link"]:hover {{ color: {t.accent_hover}; }}
    QPushButton[variant="ghost"] {{ color: {t.muted}; border-radius: 6px; }}
    QPushButton[variant="ghost"]:hover {{ background: {t.surface_alt}; color: {t.text}; }}
    QPushButton[variant="link"]:disabled, QPushButton[variant="ghost"]:disabled {{
        background: transparent; color: {t.muted};
    }}
    QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 2px; }}
    QToolButton:hover {{ background: {t.surface_alt}; }}

    /* ---- input ---- */
    QLineEdit, QComboBox, QPlainTextEdit {{
        background: {t.input_bg}; border: 1px solid {t.border_strong}; border-radius: 8px;
        padding: 7px 10px; selection-background-color: {t.accent};
        selection-color: {t.on_accent};
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {t.accent}; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {t.muted}; background: {t.surface_alt}; }}
    QLineEdit[invalid="true"] {{ border: 1px solid {t.danger}; }}
    QLineEdit#CodeInput {{
        font-size: 26px; font-weight: 600; padding: 10px;
    }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox QAbstractItemView {{
        background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 8px;
        selection-background-color: {t.accent_soft}; selection-color: {t.text}; outline: none;
        padding: 4px;
    }}
    QPlainTextEdit#Log {{
        background: {t.surface_alt}; font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
        font-size: 12px; padding: 10px;
    }}

    /* ---- drop zone ---- */
    QFrame#DropZone {{
        background: {t.surface_alt}; border: 2px dashed {t.border_strong}; border-radius: 12px;
    }}
    QFrame#DropZone:hover {{ border-color: {t.accent}; }}
    QFrame#DropZone[dragActive="true"] {{ background: {t.accent_soft}; border-color: {t.accent}; }}
    QFrame#DropZone[dragActive="reject"] {{ background: {t.danger_soft}; border-color: {t.danger}; }}
    QFrame#DropZone[loaded="true"] {{ border-style: solid; border-width: 1px; border-color: {t.border}; }}

    /* ---- progress ---- */
    QProgressBar {{
        background: {t.border}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px;
        text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}

    /* ---- banner ---- */
    QFrame[banner="success"] {{ background: {t.success_soft}; border: 1px solid {t.success}; border-radius: 10px; }}
    QFrame[banner="error"] {{ background: {t.danger_soft}; border: 1px solid {t.danger}; border-radius: 10px; }}
    QFrame[banner="warning"] {{ background: {t.warning_soft}; border: 1px solid {t.warning}; border-radius: 10px; }}
    QFrame[banner="info"] {{ background: {t.accent_soft}; border: 1px solid {t.accent}; border-radius: 10px; }}

    /* ---- pill di stato ---- */
    QLabel[pill] {{ border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}
    QLabel[pill="ok"] {{ background: {t.success_soft}; color: {t.success}; }}
    QLabel[pill="warn"] {{ background: {t.warning_soft}; color: {t.warning}; }}
    QLabel[pill="error"] {{ background: {t.danger_soft}; color: {t.danger}; }}
    QLabel[pill="neutral"] {{ background: {t.surface_alt}; color: {t.muted}; border: 1px solid {t.border}; }}
    QLabel[pill="info"] {{ background: {t.accent_soft}; color: {t.accent}; }}

    /* ---- status bar ---- */
    QStatusBar {{ background: {t.surface}; border-top: 1px solid {t.border}; color: {t.muted}; }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ color: {t.muted}; padding: 0 4px; }}

    /* ---- scrollbar ---- */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t.border_strong}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QMessageBox QLabel {{ background: transparent; }}
    """


def apply_theme(app: QApplication | None = None, dark: bool | None = None) -> Tokens:
    """Applica palette + stylesheet all'applicazione. Ritorna i token usati."""
    global _current
    app = app or QApplication.instance()
    if dark is None:
        dark = system_prefers_dark()
    _current = DARK if dark else LIGHT
    if app is not None:
        app.setStyle("Fusion")
        app.setPalette(_palette(_current))
        app.setStyleSheet(stylesheet(_current))
    return _current


def set_prop(widget: QWidget, name: str, value) -> None:
    """Imposta una proprietà dinamica e ricalcola lo stile del widget."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
