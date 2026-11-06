"""Widget riutilizzabili dell'interfaccia: card, drop zone, step, banner, badge."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.ipa import IpaInfo
from ..core.signer import readable_size
from . import icons, theme
from .theme import set_prop


# --------------------------------------------------------------- helper
def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def button(text: str, variant: str | None = None, icon_name: str | None = None,
           icon_color: str | None = None, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if icon_name:
        t = theme.current()
        color = icon_color or (t.on_accent if variant == "primary" else t.text)
        btn.setIcon(icons.icon(icon_name, color))
        btn.setIconSize(QSize(16, 16))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def icon_button(icon_name: str, tooltip: str, size: int = 18) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(icons.icon(icon_name, theme.current().muted))
    btn.setIconSize(QSize(size, size))
    btn.setToolTip(tooltip)
    btn.setAccessibleName(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def pill(text: str = "", kind: str = "neutral") -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("pill", kind)
    lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return lbl


def set_pill(lbl: QLabel, text: str, kind: str) -> None:
    lbl.setText(text)
    set_prop(lbl, "pill", kind)


def discard(widget: QWidget) -> None:
    """Toglie subito un widget dalla vista e lo distrugge al prossimo giro dell'event loop.

    Niente setParent(None): passerebbe la proprietà a Python e il widget potrebbe
    essere distrutto subito, anche mentre è in corso il suo stesso segnale (es. il
    pulsante «Rimuovi» che elimina la propria riga).
    """
    widget.hide()
    widget.deleteLater()


def icon_label(name: str, color: str, size: int = 18) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(icons.pixmap(name, color, size))
    lbl.setFixedSize(size, size)
    return lbl


def app_icon_pixmap(info_or_png, name: str, size: int) -> QPixmap:
    """Icona dell'app (PNG estratto dall'IPA) o tessera con l'iniziale."""
    png = info_or_png.icon_png if isinstance(info_or_png, IpaInfo) else info_or_png
    if png:
        src = QPixmap()
        if src.loadFromData(png) and not src.isNull():
            return icons.rounded_pixmap(src, size)
    return icons.letter_tile(name or "?", size, "#94A3B8", "#475569")


# ----------------------------------------------------------------- card
class Card(QFrame):
    """Riquadro con titolo, sottotitolo e numero di passo opzionale."""

    def __init__(self, title: str = "", subtitle: str = "", step: int | None = None, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self._step = step
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        self.badge: QLabel | None = None
        self.title_label: QLabel | None = None
        self.subtitle_label: QLabel | None = None
        self.header_right = QHBoxLayout()
        self.header_right.setSpacing(6)
        if title:
            head = QHBoxLayout()
            head.setSpacing(10)
            if step is not None:
                self.badge = QLabel(str(step))
                self.badge.setProperty("role", "stepBadge")
                self.badge.setFixedSize(22, 22)
                self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            self.title_label = label(title, "cardTitle")
            col.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = label(subtitle, "muted", wrap=True)
                col.addWidget(self.subtitle_label)
            head.addLayout(col, 1)
            head.addLayout(self.header_right)
            outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)

    def set_done(self, done: bool) -> None:
        if self.badge is None:
            return
        self.badge.setText("✓" if done else str(self._step))
        set_prop(self.badge, "done", "true" if done else "false")
        self.badge.setToolTip("Completato" if done else "")


# ------------------------------------------------------------ drop zone
class DropZone(QFrame):
    """Area per trascinare o scegliere un .ipa; mostra i dettagli quando caricato."""

    fileDropped = Signal(Path)
    browseRequested = Signal()
    clearRequested = Signal()
    invalidDropped = Signal(str)

    ICON_SIZE = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(118)
        self._info: IpaInfo | None = None
        t = theme.current()

        self._stack = QStackedLayout(self)

        # --- vuota
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(20, 18, 20, 18)
        ev.setSpacing(6)
        ev.addStretch(1)
        self._empty_icon = icon_label("upload", t.accent, 30)
        ev.addWidget(self._empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_title = label("Trascina qui il file .ipa", "title")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(self._empty_title)
        row = QHBoxLayout()
        row.setSpacing(2)
        row.addStretch(1)
        row.addWidget(label("oppure", "muted"))
        self.btn_browse = button("scegli un file dal computer", "link")
        self.btn_browse.clicked.connect(self.browseRequested)
        row.addWidget(self.btn_browse)
        row.addStretch(1)
        ev.addLayout(row)
        ev.addStretch(1)
        self._stack.addWidget(empty)

        # --- caricata
        loaded = QWidget()
        lh = QHBoxLayout(loaded)
        lh.setContentsMargins(14, 12, 10, 12)
        lh.setSpacing(14)
        self.app_icon = QLabel()
        self.app_icon.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        lh.addWidget(self.app_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.app_name = label("", "title")
        self.app_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.app_meta = label("", "muted")
        self.app_bundle = label("", "small")
        self.app_bundle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addStretch(1)
        col.addWidget(self.app_name)
        col.addWidget(self.app_meta)
        col.addWidget(self.app_bundle)
        col.addStretch(1)
        lh.addLayout(col, 1)
        self.btn_change = button("Cambia…", "ghost")
        self.btn_change.clicked.connect(self.browseRequested)
        lh.addWidget(self.btn_change, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_clear = icon_button("x", "Rimuovi il file selezionato", 16)
        self.btn_clear.clicked.connect(self.clearRequested)
        lh.addWidget(self.btn_clear, 0, Qt.AlignmentFlag.AlignVCenter)
        self._stack.addWidget(loaded)

        self.set_info(None)

    # --------------------------------------------------------------- API
    @property
    def info(self) -> IpaInfo | None:
        return self._info

    def set_info(self, info: IpaInfo | None) -> None:
        self._info = info
        set_prop(self, "loaded", "true" if info else "false")
        if info is None:
            self._stack.setCurrentIndex(0)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Trascina un file .ipa oppure fai clic per sceglierlo")
            return
        self.unsetCursor()
        self.setToolTip(str(info.path))
        self.app_icon.setPixmap(app_icon_pixmap(info, info.name, self.ICON_SIZE))
        self.app_name.setText(info.name)
        meta = []
        if info.version_label:
            meta.append(f"Versione {info.version_label}")
        if info.min_os:
            meta.append(f"iOS {info.min_os}+")
        meta.append(readable_size(info.size))
        if info.extensions:
            meta.append(f"{info.extensions} estensioni" if info.extensions > 1 else "1 estensione")
        self.app_meta.setText(" · ".join(meta))
        self.app_bundle.setText(f"{info.bundle_id}  —  {info.path.name}")
        self._stack.setCurrentIndex(1)

    # ------------------------------------------------------------ eventi
    def mouseReleaseEvent(self, e):
        if self._info is None and e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.browseRequested.emit()
        super().mouseReleaseEvent(e)

    @staticmethod
    def _ipa_from(mime) -> Path | None:
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.suffix.lower() == ".ipa":
                    return p
        return None

    def dragEnterEvent(self, e):
        if not e.mimeData().hasUrls():
            e.ignore()
            return
        ok = self._ipa_from(e.mimeData()) is not None
        set_prop(self, "dragActive", "true" if ok else "reject")
        e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self._ipa_from(e.mimeData()) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        set_prop(self, "dragActive", "false")
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        set_prop(self, "dragActive", "false")
        path = self._ipa_from(e.mimeData())
        if path is None:
            self.invalidDropped.emit("Il file trascinato non è un .ipa")
            e.ignore()
            return
        e.acceptProposedAction()
        self.fileDropped.emit(path)


# -------------------------------------------------------- step indicator
class StepIndicator(QWidget):
    """Sequenza di passi con stato: completato, in corso, in attesa, fallito."""

    def __init__(self, steps: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._steps = steps
        self._current = -1
        self._failed = False
        self._finished = False
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(600, 54)

    def reset(self) -> None:
        self._current, self._failed, self._finished = -1, False, False
        self.update()

    def set_current(self, key: str) -> None:
        if key == "done":
            self._current, self._finished = len(self._steps), True
        else:
            keys = [k for k, _ in self._steps]
            if key in keys:
                self._current = keys.index(key)
        self.update()

    def set_failed(self) -> None:
        self._failed = True
        self.update()

    def state_of(self, index: int) -> str:
        if self._finished or index < self._current:
            return "done"
        if index == self._current:
            return "failed" if self._failed else "active"
        return "pending"

    def current_label(self) -> str:
        if 0 <= self._current < len(self._steps):
            return self._steps[self._current][1]
        return ""

    def paintEvent(self, _e):
        t = theme.current()
        n = len(self._steps)
        if n == 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        slot = w / n
        r = 11
        cy = 14
        font = QFont(self.font())
        font.setPixelSize(11)
        bold = QFont(font)
        bold.setBold(True)
        fm = QFontMetrics(font)

        # linee di collegamento
        for i in range(n - 1):
            x1 = slot * i + slot / 2 + r + 3
            x2 = slot * (i + 1) + slot / 2 - r - 3
            done = self.state_of(i) == "done" and self.state_of(i + 1) != "pending"
            p.setPen(QPen(QColor(t.success if done else t.border_strong), 2))
            p.drawLine(QPointF(x1, cy), QPointF(x2, cy))

        for i, (_key, text) in enumerate(self._steps):
            cx = slot * i + slot / 2
            state = self.state_of(i)
            circle = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            if state == "done":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.success))
                p.drawEllipse(circle)
                p.drawPixmap(circle.adjusted(5, 5, -5, -5).toRect(), icons.pixmap("check", "#FFFFFF", 12, 3))
                color = t.text
            elif state == "active":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.accent))
                p.drawEllipse(circle)
                p.setPen(QColor(t.on_accent))
                p.setFont(bold)
                p.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(i + 1))
                color = t.text
            elif state == "failed":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.danger))
                p.drawEllipse(circle)
                p.drawPixmap(circle.adjusted(5, 5, -5, -5).toRect(), icons.pixmap("x", "#FFFFFF", 12, 3))
                color = t.danger
            else:
                p.setPen(QPen(QColor(t.border_strong), 2))
                p.setBrush(QColor(t.surface))
                p.drawEllipse(circle.adjusted(1, 1, -1, -1))
                p.setPen(QColor(t.muted))
                p.setFont(font)
                p.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(i + 1))
                color = t.muted
            p.setFont(bold if state in ("active", "failed") else font)
            p.setPen(QColor(color))
            elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(slot) - 4)
            p.drawText(QRectF(slot * i, cy + r + 4, slot, 18), Qt.AlignmentFlag.AlignHCenter, elided)
        p.end()


# ---------------------------------------------------------------- banner
class Banner(QFrame):
    """Messaggio in evidenza (successo, errore, avviso, info) con azioni."""

    _ICONS = {"success": "check-circle", "error": "x-circle", "warning": "alert", "info": "info"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("banner", "info")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 8, 12)
        lay.setSpacing(12)
        self._icon = QLabel()
        self._icon.setFixedSize(22, 22)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title = label("", "title", wrap=True)
        self.message = label("", wrap=True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint = label("", "muted", wrap=True)
        col.addWidget(self.title)
        col.addWidget(self.message)
        col.addWidget(self.hint)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        self._actions.setContentsMargins(0, 6, 0, 0)
        col.addLayout(self._actions)
        lay.addLayout(col, 1)
        self.btn_close = icon_button("x", "Chiudi", 14)
        self.btn_close.clicked.connect(self.hide)
        lay.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)
        self.kind = "info"
        self.hide()

    def show_message(self, kind: str, title: str, message: str = "", hint: str = "",
                     actions: list[tuple[str, object]] | None = None) -> None:
        t = theme.current()
        color = {"success": t.success, "error": t.danger, "warning": t.warning}.get(kind, t.accent)
        self.kind = kind
        set_prop(self, "banner", kind)
        self._icon.setPixmap(icons.pixmap(self._ICONS.get(kind, "info"), color, 22))
        self.title.setText(title)
        self.message.setText(message)
        self.message.setVisible(bool(message))
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))
        while self._actions.count():
            item = self._actions.takeAt(0)
            if item.widget():
                discard(item.widget())
        for text, callback in actions or []:
            btn = button(text)
            btn.clicked.connect(callback)
            self._actions.addWidget(btn)
        self._actions.addStretch(1)
        self.show()


# ---------------------------------------------------------- voce recenti
class RecentItem(QFrame):
    """Riga dello storico installazioni."""

    reinstallRequested = Signal(dict)
    removeRequested = Signal(dict)

    def __init__(self, entry: dict, icon_png: bytes | None, expiry: tuple[str, str],
                 subtitle: str, can_reinstall: bool, reinstall_tip: str = "", parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setProperty("card", True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setFixedSize(40, 40)
        ic.setPixmap(app_icon_pixmap(icon_png, entry.get("app_name", "?"), 40))
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = label(entry.get("app_name") or entry.get("bundle_id", ""), "title")
        col.addWidget(self.name)
        self.sub = label(subtitle, "muted")
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.expiry = pill(*expiry)
        lay.addWidget(self.expiry)
        self.btn_reinstall = button("Reinstalla", icon_name="refresh")
        self.btn_reinstall.setEnabled(can_reinstall)
        if reinstall_tip:
            self.btn_reinstall.setToolTip(reinstall_tip)
        self.btn_reinstall.clicked.connect(lambda: self.reinstallRequested.emit(self.entry))
        lay.addWidget(self.btn_reinstall)
        self.btn_remove = icon_button("trash", "Rimuovi dall'elenco", 16)
        self.btn_remove.clicked.connect(lambda: self.removeRequested.emit(self.entry))
        lay.addWidget(self.btn_remove)
