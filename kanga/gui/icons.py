"""Icone vettoriali (SVG a tratto) colorate al volo secondo il tema.

Disegnate per questo progetto: niente dipendenze da set di icone esterni.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "refresh": '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6"/><path d="M20.5 3.5v5h-5"/>',
    "phone": '<rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M10.5 18.5h3"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
    "upload": '<path d="M12 15V4"/><path d="M7 9l5-5 5 5"/>'
              '<path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    "alert": '<path d="M12 3.5L2.5 20h19z"/><path d="M12 10v4.5"/><path d="M12 17.5v.01"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5'
              'a2 2 0 0 1-2-2z"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/>'
            '<path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "trash": '<path d="M4 7h16"/><path d="M9.5 7V4.5h5V7"/><path d="M6 7l1 13h10l1-13"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "logout": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 8l-4 4 4 4"/>'
              '<path d="M6 12h10"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8v.01"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    "shield": '<path d="M12 3l7 3v5.5c0 4.5-3 8-7 9.5-4-1.5-7-5-7-9.5V6z"/><path d="M9 12l2 2 4-4"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/>'
            '<path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'
               '<path d="M3 3l18 18"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3"/><path d="M12 15h5"/>',
    "package": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5"/><path d="M12 12v9"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "wifi": '<path d="M2.5 9a14 14 0 0 1 19 0"/><path d="M5.5 12.5a9.5 9.5 0 0 1 13 0"/>'
            '<path d="M8.5 16a5 5 0 0 1 7 0"/><path d="M12 19.5v.01"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M10 10h4v4h-4z"/>'
           '<path d="M9 2.5V6M15 2.5V6M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5"/>',
    "unlock":'<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 7.6-1.7"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/>'
                '<path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
}


def svg(name: str, color: str, stroke: float = 2.0) -> bytes:
    body = _PATHS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    ).encode()


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int = 20, stroke: float = 2.0, dpr: float = 2.0) -> QPixmap:
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color, stroke)))
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px


def icon(name: str, color: str, size: int = 20) -> QIcon:
    ic = QIcon()
    for s in (16, 20, 24, 32):
        ic.addPixmap(pixmap(name, color, s))
    return ic


def rounded_pixmap(source: QPixmap, size: int, radius_ratio: float = 0.225, dpr: float = 2.0) -> QPixmap:
    """Ritaglia un'immagine con angoli arrotondati (stile icona iOS)."""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    r = px.width() * radius_ratio
    path.addRoundedRect(QRectF(0, 0, px.width(), px.height()), r, r)
    painter.setClipPath(path)
    scaled = source.scaled(
        px.width(), px.height(),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px


def letter_tile(text: str, size: int, start: str, end: str, dpr: float = 2.0,
                radius_ratio: float = 0.225, fg: str = "#FFFFFF") -> QPixmap:
    """Tessera colorata con l'iniziale (icona di riserva / avatar / logo)."""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(0, 0, px.width(), px.height())
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor(start))
    grad.setColorAt(1, QColor(end))
    path = QPainterPath()
    if radius_ratio >= 0.5:
        path.addEllipse(rect)
    else:
        r = px.width() * radius_ratio
        path.addRoundedRect(rect, r, r)
    painter.fillPath(path, grad)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(px.height() * 0.46))
    painter.setFont(font)
    painter.setPen(QColor(fg))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, (text or "?")[:1].upper())
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px


def app_logo(size: int = 32) -> QPixmap:
    return letter_tile("K", size, "#3B82F6", "#1E3A8A")


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(letter_tile("K", s, "#3B82F6", "#1E3A8A", dpr=1.0))
    return ic


def icon_tile(name: str, size: int, bg: str, fg: str, dpr: float = 2.0) -> QPixmap:
    """Cerchio colorato con un'icona al centro (avatar di riserva)."""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(bg))
    painter.drawEllipse(QRectF(0, 0, px.width(), px.height()))
    inner = int(size * 0.55)
    off = (size - inner) / 2 * dpr
    painter.drawPixmap(QRectF(off, off, inner * dpr, inner * dpr).toRect(), pixmap(name, fg, inner))
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px
