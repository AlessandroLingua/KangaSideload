"""Finestra principale di Kanga Sideload.

Quattro sezioni (Installa · Recenti · Jailbreak · Registro). La pagina Installa
guida in tre passi (account, dispositivo, app) e spiega sempre cosa manca per
procedere; la pagina Jailbreak scarica i tool compatibili e li passa alla
stessa pipeline di firma e installazione.
Tutte le operazioni lente girano in thread separati; i risultati tornano al
thread principale tramite segnali Qt.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import device as device_mod
from ..core import signer, store
from ..core.appflow import STEPS, AppFlow, SideloadResult
from ..core.device import DeviceInfo
from ..core.errors import friendly_error
from ..core.ipa import IpaError, IpaInfo, read_ipa_info
from ..core.signer import readable_size
from . import format as fmt
from . import icons, theme
from .jailbreak_page import JailbreakPage
from .login_dialog import LoginDialog
from .theme import set_prop
from .widgets import (
    Banner, Card, DropZone, RecentItem, StepIndicator, button, discard, label, pill, set_pill,
)
from .workers import FlowBridge, start_worker

logger = logging.getLogger(__name__)

PAGE_INSTALL, PAGE_RECENT, PAGE_JAILBREAK, PAGE_LOG = 0, 1, 2, 3
NAV_PAGES = [
    ("Installa", "package"),
    ("Recenti", "clock"),
    ("Jailbreak", "unlock"),
    ("Registro", "terminal"),
]
POLL_INTERVAL_MS = 2500
_PROGRESS_MAX = 1000


def _scroll_page(content: QWidget, max_width: int = 820) -> QScrollArea:
    """Pagina scorrevole con colonna centrale a larghezza massima."""
    outer = QWidget()
    outer.setObjectName("ScrollContent")
    h = QHBoxLayout(outer)
    h.setContentsMargins(0, 0, 0, 0)
    h.addStretch(1)
    content.setMaximumWidth(max_width)
    content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    h.addWidget(content, 100)
    h.addStretch(1)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidget(outer)
    return area


class MainWindow(QMainWindow):
    def __init__(self, flow: AppFlow | None = None, devices=None,
                 settings: QSettings | None = None, auto_refresh: bool = True):
        super().__init__()
        self.setWindowTitle("Kanga Sideload")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(720, 600)
        self.resize(880, 780)
        self.setAcceptDrops(True)

        self._settings = settings or QSettings("Kanga", "KangaSideload")
        self._bridge = FlowBridge(self._on_flow_event, self)
        self.flow = flow or AppFlow()
        self.flow.on_event = self._bridge.emit
        self.devices = devices or device_mod

        self._devices: list[DeviceInfo] = []
        self._known_serials: list[str] | None = None
        self._usbmux_ok: bool | None = None
        self._zsign_ok = False
        self._has_session = False
        self._ipa_info: IpaInfo | None = None
        self._busy = False
        self._scan_running = False
        self._poll_running = False
        self._untrusted_ticks = 0
        self._install_ctx: tuple[IpaInfo, DeviceInfo, str | None] | None = None
        self._step_index = 0
        self.last_result: SideloadResult | None = None

        root = QWidget()
        root.setObjectName("Page")
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_header())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_install_page())
        self.pages.addWidget(self._build_recent_page())
        self.pages.addWidget(self._build_jailbreak_page())
        self.pages.addWidget(self._build_log_page())
        rl.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self._build_statusbar()
        self._build_shortcuts()

        geometry = self._settings.value("geometry")
        if isinstance(geometry, (QByteArray, bytes)):
            self.restoreGeometry(geometry)

        self._log(f"Kanga Sideload {__version__} avviato.")
        self._refresh_account()
        self._update_tools_status()
        self._reload_history()
        self._update_device_status()
        self._check_expiring()

        self._poll = QTimer(self)
        self._poll.setInterval(POLL_INTERVAL_MS)
        self._poll.timeout.connect(self._poll_devices)
        if auto_refresh:
            QTimer.singleShot(0, self.refresh_devices)
            self._poll.start()

    # ================================================================ build
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        h = QHBoxLayout(header)
        h.setContentsMargins(20, 12, 20, 12)
        h.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(icons.app_logo(36))
        h.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel("Kanga Sideload")
        title.setObjectName("AppTitle")
        sub = QLabel("Installa app sul tuo iPhone con il tuo Apple ID")
        sub.setObjectName("AppSubtitle")
        col.addWidget(title)
        col.addWidget(sub)
        h.addLayout(col)
        h.addStretch(1)

        nav = QFrame()
        nav.setObjectName("NavBar")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(3, 3, 3, 3)
        nl.setSpacing(2)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for idx, (text, icon_name) in enumerate(NAV_PAGES):
            b = QPushButton(text)
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setIcon(icons.icon(icon_name, theme.current().muted))
            b.setIconSize(QSize(15, 15))
            b.setToolTip(f"{text} (Ctrl+{idx + 1})")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav_group.addButton(b, idx)
            self.nav_buttons.append(b)
            nl.addWidget(b)
        self.nav_buttons[0].setChecked(True)
        self.nav_group.idClicked.connect(self.show_page)
        h.addWidget(nav)
        return header

    def _build_install_page(self) -> QWidget:
        content = QWidget()
        content.setObjectName("ScrollContent")
        v = QVBoxLayout(content)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(14)

        # --- 1. account
        self.card_account = Card("Account Apple", step=1)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.avatar = QLabel()
        self.avatar.setFixedSize(40, 40)
        row.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.lbl_account = label("", "title")
        self.lbl_account_sub = label("", "muted", wrap=True)
        col.addWidget(self.lbl_account)
        col.addWidget(self.lbl_account_sub)
        row.addLayout(col, 1)
        self.btn_account = button("Accedi", "primary")
        self.btn_account.clicked.connect(self._on_account_clicked)
        row.addWidget(self.btn_account)
        self.card_account.body.addLayout(row)
        v.addWidget(self.card_account)

        # --- 2. dispositivo
        self.card_device = Card("Dispositivo", step=2)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.cmb_devices = QComboBox()
        self.cmb_devices.setPlaceholderText("Nessun dispositivo collegato")
        self.cmb_devices.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cmb_devices.setIconSize(QSize(16, 16))
        self.cmb_devices.setAccessibleName("Dispositivo di destinazione")
        self.cmb_devices.currentIndexChanged.connect(self._on_device_selected)
        row.addWidget(self.cmb_devices, 1)
        self.btn_refresh = button("Aggiorna", icon_name="refresh", tooltip="Cerca di nuovo i dispositivi (F5)")
        self.btn_refresh.clicked.connect(self.refresh_devices)
        row.addWidget(self.btn_refresh)
        self.card_device.body.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.device_pill = pill("", "neutral")
        row.addWidget(self.device_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lbl_device_help = label("", "muted", wrap=True)
        row.addWidget(self.lbl_device_help, 1)
        self.btn_pair = button("Abbina", icon_name="link", tooltip="Chiedi all'iPhone di autorizzare questo computer")
        self.btn_pair.clicked.connect(self._pair_device)
        self.btn_pair.hide()
        row.addWidget(self.btn_pair)
        self.card_device.body.addLayout(row)
        v.addWidget(self.card_device)

        # --- 3. app
        self.card_app = Card("App da installare", step=3)
        self.drop = DropZone()
        self.drop.browseRequested.connect(self.browse_ipa)
        self.drop.fileDropped.connect(self.load_ipa)
        self.drop.clearRequested.connect(self.clear_ipa)
        self.drop.invalidDropped.connect(lambda msg: self.statusBar().showMessage(msg, 4000))
        self.card_app.body.addWidget(self.drop)
        row = QHBoxLayout()
        row.setSpacing(10)
        name_lbl = label("Nome sulla Home", "muted")
        row.addWidget(name_lbl)
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Facoltativo: lascia vuoto per il nome originale")
        self.txt_name.setAccessibleName("Nome dell'app sulla schermata Home")
        self.txt_name.setMaxLength(40)
        name_lbl.setBuddy(self.txt_name)
        row.addWidget(self.txt_name, 1)
        self.card_app.body.addLayout(row)
        v.addWidget(self.card_app)

        # --- azione
        action = QHBoxLayout()
        action.setSpacing(12)
        self.ready_icon = QLabel()
        self.ready_icon.setFixedSize(18, 18)
        action.addWidget(self.ready_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lbl_ready = label("", "muted", wrap=True)
        action.addWidget(self.lbl_ready, 1)
        self.btn_install = button("Firma e installa", "primary", icon_name="play",
                                  tooltip="Firma l'app e installala sul dispositivo (Ctrl+Invio)")
        self.btn_install.setMinimumHeight(42)
        self.btn_install.setMinimumWidth(190)
        self.btn_install.setStyleSheet("padding: 10px 22px; font-size: 14px;")
        self.btn_install.clicked.connect(self.start_install)
        action.addWidget(self.btn_install)
        v.addLayout(action)

        # --- avanzamento
        self.card_progress = Card()
        self.steps = StepIndicator(STEPS)
        self.card_progress.body.addWidget(self.steps)
        self.progress = QProgressBar()
        self.progress.setRange(0, _PROGRESS_MAX)
        self.card_progress.body.addWidget(self.progress)
        self.lbl_progress = label("", "muted", wrap=True)
        self.card_progress.body.addWidget(self.lbl_progress)
        self.card_progress.hide()
        v.addWidget(self.card_progress)

        self.banner = Banner()
        v.addWidget(self.banner)
        v.addStretch(1)

        self.install_scroll = _scroll_page(content)
        return self.install_scroll

    def _build_recent_page(self) -> QWidget:
        content = QWidget()
        content.setObjectName("ScrollContent")
        v = QVBoxLayout(content)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(10)
        v.addWidget(label("Installazioni recenti", "h2"))
        v.addWidget(label(
            "Con un Apple ID gratuito le app smettono di aprirsi dopo 7 giorni: "
            "reinstallale prima della scadenza per rinnovare la firma.", "muted", wrap=True))

        self.recent_empty = Card()
        t = theme.current()
        ic = QLabel()
        ic.setPixmap(icons.icon_tile("clock", 44, t.accent_soft, t.accent))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_empty.body.addWidget(ic)
        msg = label("Nessuna installazione recente", "title")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_empty.body.addWidget(msg)
        sub = label("Le app che installi con Kanga compaiono qui, con la data di scadenza.", "muted", wrap=True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_empty.body.addWidget(sub)
        v.addWidget(self.recent_empty)

        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(8)
        v.addLayout(self.recent_box)
        v.addStretch(1)
        self.recent_items: list[RecentItem] = []
        return _scroll_page(content)

    def _build_jailbreak_page(self) -> QWidget:
        self.jailbreak = JailbreakPage()
        self.jailbreak.logMessage.connect(self._log)
        self.jailbreak.installRequested.connect(self.install_from_file)
        return _scroll_page(self.jailbreak)

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(label("Registro attività", "h2"))
        bar.addStretch(1)
        b_copy = button("Copia", icon_name="copy", tooltip="Copia tutto il registro negli appunti")
        b_copy.clicked.connect(self._copy_log)
        b_save = button("Salva…", icon_name="download", tooltip="Salva il registro in un file di testo")
        b_save.clicked.connect(self._save_log)
        b_clear = button("Pulisci", icon_name="trash")
        b_clear.clicked.connect(lambda: self.txt_log.clear())
        b_dir = button("Cartella dati", icon_name="folder", tooltip=str(store.base_dir()))
        b_dir.clicked.connect(lambda: self._open_path(store.base_dir()))
        for b in (b_copy, b_save, b_clear, b_dir):
            bar.addWidget(b)
        v.addLayout(bar)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setObjectName("Log")
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(5000)
        self.txt_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        v.addWidget(self.txt_log, 1)
        return page

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.sb_usbmux = pill("Apple Mobile Device…", "neutral")
        self.sb_usbmux.setToolTip("Servizio che permette di comunicare con iPhone e iPad via USB")
        self.sb_zsign = pill("zsign…", "neutral")
        self.sb_zsign.setToolTip("Programma usato per firmare le app")
        self.btn_fetch_zsign = button("Scarica zsign", "link", icon_name="download",
                                      icon_color=theme.current().accent)
        self.btn_fetch_zsign.clicked.connect(self._fetch_zsign)
        self.btn_fetch_zsign.hide()
        sb.addWidget(self.sb_usbmux)
        sb.addWidget(self.sb_zsign)
        sb.addWidget(self.btn_fetch_zsign)
        sb.addPermanentWidget(QLabel(f"v{__version__}"))

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.Open, self, activated=self.browse_ipa)
        QShortcut(QKeySequence(Qt.Key.Key_F5), self, activated=self.refresh_devices)
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, activated=self.start_install)
        for i in range(len(NAV_PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.show_page(i))

    # ============================================================= helpers
    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        for line in (str(msg).splitlines() or [""]):
            self.txt_log.appendPlainText(f"{ts}  {line}")
        logger.info(msg)

    def _copy_log(self) -> None:
        QGuiApplication.clipboard().setText(self.txt_log.toPlainText())
        self.statusBar().showMessage("Registro copiato negli appunti", 3000)

    def _save_log(self) -> None:
        default = str(Path.home() / f"kanga-registro-{datetime.now():%Y%m%d-%H%M}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Salva registro", default, "Testo (*.txt)")
        if path:
            Path(path).write_text(self.txt_log.toPlainText(), encoding="utf-8")
            self.statusBar().showMessage(f"Registro salvato in {path}", 4000)

    @staticmethod
    def _open_path(path: Path) -> None:
        path = Path(path)
        if path.is_dir() or not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_flow_event(self, tag: str, data) -> None:
        if tag == "log":
            text = str(data)
            self._log(text)
            if self._busy and not text.startswith("  "):
                self.lbl_progress.setText(text)
        elif tag == "step":
            self.steps.set_current(str(data))
            keys = [k for k, _ in STEPS]
            if data in keys:
                self._step_index = keys.index(data)
                self._set_progress(self._step_index / len(STEPS))
            elif data == "done":
                self._set_progress(1.0)
        elif tag == "progress":
            pct, state = data if isinstance(data, tuple) else (None, str(data))
            if pct is None:
                self.lbl_progress.setText(f"{state or 'Installazione'}…")
            else:
                base = self._step_index / len(STEPS)
                self._set_progress(base + (min(int(pct), 100) / 100) / len(STEPS))
                self.lbl_progress.setText(f"Installazione sul dispositivo: {int(pct)}%")

    def _set_progress(self, fraction: float) -> None:
        if self.progress.maximum() == 0:
            self.progress.setRange(0, _PROGRESS_MAX)
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * _PROGRESS_MAX))

    # ============================================================ account
    def _refresh_account(self) -> None:
        t = theme.current()
        self._has_session = self.flow.has_session()
        name = self.flow.account_name()
        if self._has_session:
            self.avatar.setPixmap(icons.letter_tile(name or "A", 40, t.accent, t.accent_pressed, radius_ratio=0.5))
            self.lbl_account.setText(name or "Account Apple")
            self.lbl_account_sub.setText("Connesso · la sessione resta salvata su questo computer")
            self.btn_account.setText("Esci")
            set_prop(self.btn_account, "variant", "")
            self.btn_account.setIcon(icons.icon("logout", t.text))
            self.btn_account.setToolTip("Esci dall'account Apple su questo computer")
        elif self._auto_login_available():
            # sessione assente/scaduta ma password salvata: l'accesso parte da solo
            saved = store.saved_apple_id()
            self.avatar.setPixmap(icons.letter_tile(saved or "A", 40, t.surface_alt, t.muted, radius_ratio=0.5))
            self.lbl_account.setText(saved or "Account Apple")
            self.lbl_account_sub.setText("Password salvata su questo computer: l'accesso è automatico "
                                         "quando installi (serve solo il codice di verifica).")
            self.btn_account.setText("Accedi")
            set_prop(self.btn_account, "variant", "primary")
            self.btn_account.setIcon(icons.icon("user", t.on_accent))
            self.btn_account.setToolTip("Accedi ora con la password salvata")
        else:
            self.avatar.setPixmap(icons.icon_tile("user", 40, t.surface_alt, t.muted))
            self.lbl_account.setText("Non connesso")
            self.lbl_account_sub.setText("Accedi con il tuo Apple ID per firmare le app.")
            self.btn_account.setText("Accedi")
            set_prop(self.btn_account, "variant", "primary")
            self.btn_account.setIcon(icons.icon("user", t.on_accent))
            self.btn_account.setToolTip("Accedi con il tuo Apple ID")
        self.card_account.set_done(self._has_session)
        self._update_ready()

    @staticmethod
    def _auto_login_available() -> bool:
        return store.credentials_supported() and store.has_credentials()

    def _create_login_dialog(self) -> LoginDialog:
        saved = store.load_credentials() if store.credentials_supported() else None
        if saved:
            # password salvata (DPAPI): il dialog parte da solo, chiede solo l'eventuale 2FA
            apple_id, password = saved
            self._log(f"Accesso automatico con le credenziali salvate ({apple_id})…")
            return LoginDialog(self.flow, self, apple_id=apple_id, password=password,
                               remember=True, auto_submit=True)
        return LoginDialog(self.flow, self, apple_id=str(self._settings.value("last_apple_id", "") or ""))

    def _login(self) -> bool:
        """Apre il dialog di login; True se l'accesso è riuscito."""
        dlg = self._create_login_dialog()
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        if ok:
            self._settings.setValue("last_apple_id", dlg.apple_id)
            self._log("Accesso effettuato: firma e installazione abilitate.")
            self.statusBar().showMessage("Accesso effettuato", 4000)
        self._refresh_account()
        return ok

    def _on_account_clicked(self) -> None:
        if self._busy:
            return
        if self._has_session:
            name = self.flow.account_name() or "questo account"
            answer = QMessageBox.question(
                self, "Esci dall'account",
                f"Vuoi uscire da {name}?\n\nLa sessione salvata verrà eliminata"
                + (" insieme alla password salvata" if self._auto_login_available() else "")
                + ": per installare di nuovo dovrai rifare l'accesso (con codice di verifica).",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.flow.logout()
                if self._auto_login_available():
                    # «Esci» deve davvero disconnettere: niente accesso automatico al prossimo avvio
                    store.clear_credentials()
                    self._log("Password salvata eliminata da questo computer.")
                self._log("Logout effettuato.")
                self._refresh_account()
            return
        self._login()

    # ============================================================ device
    def _current_device(self) -> DeviceInfo | None:
        udid = self.cmb_devices.currentData()
        return next((d for d in self._devices if d.udid == udid), None)

    def refresh_devices(self) -> None:
        if self._scan_running or self._busy:
            return
        self._scan_running = True
        self.btn_refresh.setEnabled(False)
        if not self._devices:
            set_pill(self.device_pill, "Ricerca…", "neutral")
            self.lbl_device_help.setText("Ricerca dei dispositivi collegati…")
        backend = self.devices

        def run():
            ok = backend.usbmux_available()
            return ok, (backend.list_devices() if ok else [])

        start_worker(run, on_ok=self._on_devices_scanned, on_fail=self._on_devices_failed)

    def _on_devices_failed(self, exc) -> None:
        self._scan_running = False
        self.btn_refresh.setEnabled(not self._busy)
        fe = friendly_error(exc)
        self._log(f"⚠ Ricerca dispositivi: {fe.detail}")
        set_pill(self.device_pill, "Errore", "error")
        self.lbl_device_help.setText(fe.hint or fe.message)

    def _on_devices_scanned(self, result) -> None:
        self._scan_running = False
        self.btn_refresh.setEnabled(not self._busy)
        ok, devices = result
        self._set_usbmux(ok)
        self._known_serials = sorted(d.udid for d in devices) if ok else None

        old = {d.udid: d for d in self._devices}
        new = {d.udid: d for d in devices}
        for udid, d in new.items():
            if udid not in old:
                self._log(f"Dispositivo collegato: {fmt.device_label(d)}")
            elif d.trusted and not old[udid].trusted:
                self._log(f"Dispositivo autorizzato: {fmt.device_title(d)}")
        for udid, d in old.items():
            if udid not in new:
                self._log(f"Dispositivo scollegato: {fmt.device_title(d)}")

        previous = self.cmb_devices.currentData() or self._settings.value("last_udid", "")
        self._devices = list(devices)
        t = theme.current()
        self.cmb_devices.blockSignals(True)
        self.cmb_devices.clear()
        for d in self._devices:
            icon_name = "wifi" if d.is_network else "phone"
            self.cmb_devices.addItem(icons.icon(icon_name, t.text), fmt.device_label(d), d.udid)
            if d.product_type:
                self.cmb_devices.setItemData(self.cmb_devices.count() - 1,
                                             f"{d.product_type} · {d.udid}", Qt.ItemDataRole.ToolTipRole)
        idx = self.cmb_devices.findData(previous) if previous else -1
        if idx < 0 and self._devices:
            idx = next((i for i, d in enumerate(self._devices) if d.trusted), 0)
        self.cmb_devices.setCurrentIndex(idx)
        self.cmb_devices.setEnabled(bool(self._devices) and not self._busy)
        self.cmb_devices.blockSignals(False)
        self._update_device_status()

    def _on_device_selected(self, _index: int) -> None:
        d = self._current_device()
        if d is not None:
            self._settings.setValue("last_udid", d.udid)
        self._update_device_status()

    def _set_usbmux(self, ok: bool) -> None:
        if ok != self._usbmux_ok:
            if not ok:
                self._log("⚠ Servizio Apple Mobile Device non trovato: installa iTunes (versione "
                          "classica) o l'app «Apple Devices» dal Microsoft Store.")
            elif self._usbmux_ok is False:
                self._log("Servizio Apple Mobile Device disponibile.")
        self._usbmux_ok = ok
        if ok:
            set_pill(self.sb_usbmux, "Apple Mobile Device attivo", "ok")
        else:
            set_pill(self.sb_usbmux, "Apple Mobile Device assente", "error")

    def _update_device_status(self) -> None:
        d = self._current_device()
        self.btn_pair.hide()
        if self._usbmux_ok is False:
            set_pill(self.device_pill, "Servizio assente", "error")
            self.lbl_device_help.setText(
                "Installa iTunes (versione classica) o l'app «Apple Devices» dal Microsoft "
                "Store per comunicare con iPhone e iPad.")
        elif d is None:
            if self._usbmux_ok is None and self._scan_running:
                set_pill(self.device_pill, "Ricerca…", "neutral")
                self.lbl_device_help.setText("Ricerca dei dispositivi collegati…")
            else:
                set_pill(self.device_pill, "Nessun dispositivo", "neutral")
                self.lbl_device_help.setText("Collega l'iPhone o l'iPad con un cavo USB e sbloccalo.")
        else:
            text, kind = fmt.device_status(d)
            set_pill(self.device_pill, text, kind)
            help_text = fmt.device_help(d)
            if d.trusted:
                bits = [b for b in (d.product_type, f"iOS {d.product_version}" if d.product_version else "") if b]
                help_text = " · ".join(bits) or d.udid
            self.lbl_device_help.setText(help_text)
            self.btn_pair.setVisible(not d.trusted)
            self.btn_pair.setEnabled(not self._busy)
        self.card_device.set_done(d is not None and d.trusted)
        self.jailbreak.set_device(d)
        self._update_ready()

    def _poll_devices(self) -> None:
        if self._busy or self._scan_running or self._poll_running:
            return
        self._poll_running = True
        backend = self.devices

        def run():
            try:
                return backend.list_serials()
            except Exception:
                return None

        start_worker(run, on_ok=self._on_poll_result, on_fail=self._on_poll_failed)

    def _on_poll_failed(self, _exc) -> None:
        self._poll_running = False

    def _on_poll_result(self, serials) -> None:
        self._poll_running = False
        if serials is None:
            if self._usbmux_ok is not False:
                self.refresh_devices()
            return
        if self._usbmux_ok is False or list(serials) != self._known_serials:
            self.refresh_devices()
        elif any(not d.trusted for d in self._devices):
            # in attesa di «Autorizza» sul telefono: ricontrolla ogni due giri
            self._untrusted_ticks += 1
            if self._untrusted_ticks % 2 == 0:
                self.refresh_devices()

    def _pair_device(self) -> None:
        d = self._current_device()
        if d is None or self._busy:
            return
        self.btn_pair.setEnabled(False)
        set_pill(self.device_pill, "In attesa dell'iPhone…", "info")
        self.lbl_device_help.setText("Sblocca l'iPhone e tocca «Autorizza», poi inserisci il codice.")
        self._log(f"Richiesta di autorizzazione a {fmt.device_title(d)}…")

        def ok(_info):
            self._log("Autorizzazione completata.")
            self.btn_pair.setEnabled(True)
            self.refresh_devices()

        def fail(exc):
            fe = friendly_error(exc)
            self._log(f"⚠ Abbinamento non riuscito: {fe.detail}")
            self.btn_pair.setEnabled(True)
            set_pill(self.device_pill, "Non autorizzato", "warn")
            self.lbl_device_help.setText(fe.hint or fe.message)

        start_worker(self.devices.pair, d.udid, on_ok=ok, on_fail=fail)

    # =============================================================== IPA
    def browse_ipa(self) -> None:
        if self._busy:
            return
        start = str(self._settings.value("last_dir", "") or Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Scegli un file IPA", start, "App iOS (*.ipa);;Tutti i file (*)")
        if path:
            self.load_ipa(Path(path))

    def load_ipa(self, path) -> bool:
        if self._busy:
            return False
        path = Path(path)
        try:
            info = read_ipa_info(path)
        except IpaError as e:
            fe = friendly_error(e)
            self._log(f"⚠ {path.name}: {fe.message}")
            self.banner.show_message("error", fe.title, fe.message, fe.hint)
            return False
        self._ipa_info = info
        self.drop.set_info(info)
        self.txt_name.setPlaceholderText(f"Facoltativo: lascia vuoto per «{info.name}»")
        self._settings.setValue("last_dir", str(path.parent))
        self.card_app.set_done(True)
        if self.banner.isVisible():
            self.banner.hide()
        self._log(f"IPA selezionato: {info.name} {info.version_label} — {info.bundle_id} "
                  f"({readable_size(info.size)})")
        if info.extensions:
            self._log(f"Nota: l'app contiene {info.extensions} estensioni; con un account gratuito "
                      "alcune potrebbero non funzionare.")
        self._update_ready()
        return True

    def clear_ipa(self) -> None:
        if self._busy:
            return
        self._ipa_info = None
        self.drop.set_info(None)
        self.txt_name.clear()
        self.txt_name.setPlaceholderText("Facoltativo: lascia vuoto per il nome originale")
        self.card_app.set_done(False)
        self._update_ready()

    def dragEnterEvent(self, e):
        if not self._busy and DropZone._ipa_from(e.mimeData()) is not None:
            e.acceptProposedAction()

    def dropEvent(self, e):
        path = DropZone._ipa_from(e.mimeData())
        if path is not None and not self._busy:
            e.acceptProposedAction()
            self.load_ipa(path)

    # ========================================================== readiness
    def requirements(self) -> list[str]:
        """Cosa manca per poter installare (vuoto = tutto pronto)."""
        missing = []
        if not self._has_session and not self._auto_login_available():
            missing.append("accedi con il tuo Apple ID")
        d = self._current_device()
        if d is None:
            missing.append("collega un iPhone o iPad")
        elif not d.trusted:
            missing.append("autorizza questo computer sul dispositivo")
        if self._ipa_info is None:
            missing.append("scegli un file .ipa")
        if not self._zsign_ok:
            missing.append("scarica zsign (barra in basso)")
        return missing

    def _update_ready(self) -> None:
        if not hasattr(self, "btn_install"):
            return
        t = theme.current()
        missing = self.requirements()
        self.btn_install.setEnabled(not missing and not self._busy)
        if self._busy:
            text, icon_name, color = "Installazione in corso: tieni il dispositivo collegato.", "clock", t.accent
        elif missing:
            text = "Per continuare: " + fmt.join_requirements(missing) + "."
            icon_name, color = "info", t.muted
        else:
            d = self._current_device()
            text = (f"Tutto pronto: «{self.txt_name.text().strip() or self._ipa_info.name}» "
                    f"verrà firmata e installata su {fmt.device_title(d)}.")
            icon_name, color = "check-circle", t.success
        self.lbl_ready.setText(text)
        self.ready_icon.setPixmap(icons.pixmap(icon_name, color, 18))

    # =========================================================== install
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for w in (self.btn_account, self.btn_refresh, self.btn_pair, self.drop, self.txt_name,
                  self.btn_fetch_zsign):
            w.setEnabled(not busy)
        self.cmb_devices.setEnabled(not busy and bool(self._devices))
        for item in self.recent_items:
            item.btn_reinstall.setEnabled(not busy and item.property("canReinstall") is True)
        self.jailbreak.set_busy(busy)
        self.btn_install.setText("Installazione in corso…" if busy else "Firma e installa")
        self._update_ready()

    def start_install(self) -> None:
        if self._busy or self.requirements():
            return
        session = self.flow.restore_session()
        if not session or not session.xcode_token:
            if self._auto_login_available():
                # password salvata: accesso automatico, poi l'installazione riparte da sola
                self._log("Sessione assente: accesso automatico prima dell'installazione…")
                if self._login() and self.flow.has_session():
                    self.start_install()
                return
            self._refresh_account()
            self.banner.show_message("error", "Sessione scaduta", "Accedi di nuovo con il tuo Apple ID.")
            return
        info, d = self._ipa_info, self._current_device()
        custom = self.txt_name.text().strip() or None
        self._install_ctx = (info, d, custom)
        self.banner.hide()
        self.steps.reset()
        self._step_index = 0
        self.progress.setRange(0, _PROGRESS_MAX)
        self.progress.setValue(0)
        self.lbl_progress.setText("Preparazione…")
        self.card_progress.show()
        self._set_busy(True)
        self._log(f"— Installazione di {custom or info.name} su {fmt.device_title(d)} —")
        start_worker(
            self.flow.sideload, session, info.path, d.udid,
            custom_name=custom, device_name=d.name or None,
            on_ok=self._on_install_ok, on_fail=self._on_install_failed,
        )
        QTimer.singleShot(50, lambda: self.install_scroll.ensureWidgetVisible(self.card_progress))

    def _on_install_ok(self, result: SideloadResult) -> None:
        info, d, custom = self._install_ctx
        self.last_result = result
        self._set_busy(False)
        self.steps.set_current("done")
        self._set_progress(1.0)
        self.lbl_progress.setText("Completato")
        name = custom or info.name
        expires = result.expires.isoformat() if result.expires else ""
        store.add_history_entry({
            "app_name": name,
            "custom_name": custom or "",
            "bundle_id": result.bundle_id,
            "original_bundle_id": result.original_bundle_id,
            "version": info.version_label,
            "udid": d.udid,
            "device_name": fmt.device_title(d),
            "ipa_path": str(info.path),
            "signed_ipa": str(result.signed_ipa),
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "expires": expires,
            "icon_png": self._small_icon_b64(info),
        })
        self._reload_history()
        expiry_text, _kind = fmt.expiry_status(expires)
        steps = ("Al primo avvio apri Impostazioni → Generali → VPN e gestione dispositivo, "
                 "tocca il tuo Apple ID e scegli «Autorizza».")
        try:
            major = int((d.product_version or "0").split(".")[0])
        except ValueError:
            major = 0
        if major >= 16:
            steps += " Su iOS 16 o successivi attiva anche Impostazioni → Privacy e sicurezza → Modalità sviluppatore."
        hint = (f"La firma scade il {fmt.format_date(expires)} ({expiry_text.lower()}): "
                "reinstalla l'app dalla sezione Recenti prima di allora.") if expires else ""
        self.banner.show_message(
            "success", f"«{name}» è installata su {fmt.device_title(d)}", steps, hint,
            actions=[("Mostra IPA firmato", lambda: self._open_path(result.signed_ipa.parent)),
                     ("Vai a Recenti", lambda: self.show_page(PAGE_RECENT))],
        )
        QTimer.singleShot(50, lambda: self.install_scroll.ensureWidgetVisible(self.banner))
        QApplication.alert(self)

    def _on_install_failed(self, exc) -> None:
        self._set_busy(False)
        fe = friendly_error(exc)
        self.steps.set_failed()
        self.lbl_progress.setText(f"Interrotto: {fe.title}")
        self._log(f"⚠ Errore: {fe.detail}")
        self.banner.show_message(
            "error", fe.title, fe.message, fe.hint,
            actions=[("Riprova", self.start_install), ("Mostra registro", lambda: self.show_page(PAGE_LOG))],
        )
        QTimer.singleShot(50, lambda: self.install_scroll.ensureWidgetVisible(self.banner))
        QApplication.alert(self)

    @staticmethod
    def _small_icon_b64(info: IpaInfo) -> str:
        if not info.icon_png:
            return ""
        px = QPixmap()
        if not px.loadFromData(info.icon_png):
            return ""
        px = px.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        px.save(buf, "PNG")
        return base64.b64encode(bytes(buf.data())).decode()

    # ============================================================ recenti
    def _reload_history(self) -> None:
        for item in self.recent_items:
            self.recent_box.removeWidget(item)
            discard(item)
        self.recent_items = []
        entries = store.load_history()
        for entry in entries:
            png = b""
            if entry.get("icon_png"):
                try:
                    png = base64.b64decode(entry["icon_png"])
                except Exception:
                    png = b""
            ipa_ok = bool(entry.get("ipa_path")) and Path(entry["ipa_path"]).is_file()
            sub = " · ".join(b for b in (
                entry.get("device_name", ""),
                f"installata il {fmt.format_date(entry.get('installed_at'))}" if entry.get("installed_at") else "",
                f"v{entry['version']}" if entry.get("version") else "",
            ) if b)
            item = RecentItem(
                entry, png or None, fmt.expiry_status(entry.get("expires")), sub, ipa_ok and not self._busy,
                "" if ipa_ok else f"File originale non trovato: {entry.get('ipa_path', '')}",
            )
            item.setProperty("canReinstall", ipa_ok)
            item.reinstallRequested.connect(self.reinstall)
            item.removeRequested.connect(self._remove_history)
            self.recent_box.addWidget(item)
            self.recent_items.append(item)
        self.recent_empty.setVisible(not entries)
        self.nav_buttons[PAGE_RECENT].setText(f"Recenti · {len(entries)}" if entries else "Recenti")

    def _remove_history(self, entry: dict) -> None:
        store.remove_history_entry(entry.get("bundle_id", ""), entry.get("udid", ""))
        self._reload_history()

    def reinstall(self, entry: dict) -> None:
        self.install_from_file(Path(entry.get("ipa_path", "")), custom_name=entry.get("custom_name", ""),
                               udid=entry.get("udid"), title=f"Pronto a reinstallare «{entry.get('app_name', '')}»")

    def install_from_file(self, path, custom_name: str = "", udid: str | None = None,
                          title: str = "") -> None:
        """Carica un IPA (da Recenti o dalla sezione Jailbreak) e avvia firma + installazione,
        oppure spiega cosa manca ancora."""
        if self._busy:
            return
        self.show_page(PAGE_INSTALL)
        if not self.load_ipa(Path(path)):
            return
        self.txt_name.setText(custom_name)
        if udid:
            idx = self.cmb_devices.findData(udid)
            if idx >= 0:
                self.cmb_devices.setCurrentIndex(idx)
        if not self.requirements():
            self.start_install()
        else:
            self.banner.show_message(
                "info", title or f"Pronto a installare «{self._ipa_info.name}»",
                "Per continuare: " + fmt.join_requirements(self.requirements()) + ".")

    def _check_expiring(self) -> None:
        soon = []
        for e in store.load_history():
            text, kind = fmt.expiry_status(e.get("expires"))
            if kind in ("warn", "error") and text != "Scaduta":
                soon.append(e.get("app_name", "?"))
        if soon:
            names = fmt.join_requirements([f"«{n}»" for n in soon])
            self.banner.show_message(
                "warning", "Firma in scadenza",
                f"{names} smetterà presto di aprirsi.",
                "Collega il dispositivo e usa «Reinstalla» nella sezione Recenti per rinnovarla.",
                actions=[("Vai a Recenti", lambda: self.show_page(PAGE_RECENT))],
            )

    # ============================================================ strumenti
    def _update_tools_status(self) -> None:
        self._zsign_ok = signer.zsign_available()
        if self._zsign_ok:
            set_pill(self.sb_zsign, "zsign pronto", "ok")
        else:
            set_pill(self.sb_zsign, "zsign mancante", "error")
        self.btn_fetch_zsign.setVisible(not self._zsign_ok)
        self._update_ready()

    def _fetch_zsign(self) -> None:
        from ..core.zsign_fetch import fetch_zsign

        self.btn_fetch_zsign.setEnabled(False)
        self.btn_fetch_zsign.setText("Download in corso…")
        bridge = self._bridge

        def ok(_path):
            self.btn_fetch_zsign.setText("Scarica zsign")
            self.btn_fetch_zsign.setEnabled(True)
            self._update_tools_status()
            self.statusBar().showMessage("zsign installato", 4000)

        def fail(exc):
            self.btn_fetch_zsign.setText("Scarica zsign")
            self.btn_fetch_zsign.setEnabled(True)
            self._log(f"⚠ Download di zsign non riuscito: {exc}")
            self.banner.show_message(
                "error", "Download di zsign non riuscito", str(exc),
                "Controlla la connessione oppure esegui scripts/fetch_zsign.py.")

        start_worker(fetch_zsign, log=lambda m: bridge.emit("log", m), on_ok=ok, on_fail=fail)

    # =============================================================== close
    def closeEvent(self, e):
        if self._busy:
            answer = QMessageBox.question(
                self, "Installazione in corso",
                "Un'installazione è ancora in corso. Chiudendo ora verrà interrotta.\n\nChiudere comunque?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        self._poll.stop()
        self._settings.setValue("geometry", self.saveGeometry())
        e.accept()
