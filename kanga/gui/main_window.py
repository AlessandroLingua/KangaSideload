"""Finestra principale di Kanga Sideload."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import device as device_mod
from ..core import store
from ..core.appflow import AppFlow, AppFlowError
from ..core.device import DeviceError
from ..core.gsa import GsaError
from ..core.signer import ZsignError, readable_size
from .jailbreak_tab import JailbreakTab
from .login_dialog import LoginDialog
from .workers import GenericWorker, LoginFlow, start_worker

logger = logging.getLogger(__name__)


class _GuiLogBridge(QObject):
    """Instrada i log del modulo logging verso la finestra (thread-safe via Signal)."""

    line = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)


class _GuiLogHandler(logging.Handler):
    def __init__(self, bridge: _GuiLogBridge):
        super().__init__()
        self._bridge = bridge
        self.setFormatter(logging.Formatter("%(levelname).1s %(name)s: %(message)s"))

    def emit(self, record):
        try:
            self._bridge.line.emit(self.format(record))
        except Exception:
            pass


class MainWindow(QMainWindow):
    # signal per aggiornamenti thread-safe: i worker li emettono da qualunque
    # thread e Qt li recapita nel thread GUI
    _log_sig = Signal(str)
    _flow_sig = Signal(str, str)
    session_ready = Signal()  # emesso dopo un login riuscito

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kanga Sideload")
        self.resize(780, 620)
        self.setAcceptDrops(True)

        self.ipa_path: Path | None = None
        self.flow = AppFlow(on_event=self._on_flow_event)
        self._worker: GenericWorker | None = None
        self._login_flow: LoginFlow | None = None
        self._login_dlg = None
        # ultimo tentativo di login, solo in memoria: serve a ripresentare
        # Apple ID e password se il login fallisce e va ripetuto
        self._last_login: tuple[str, str] | None = None
        self._remember_choice = True
        self._last_login_error = ""
        self._login_cancelled = False
        self._devices: list = []
        self._pending_install = False

        tabs = QTabWidget()
        tabs.addTab(self._build_sideload_tab(), "Sideload")
        tabs.addTab(JailbreakTab(self), "Jailbreak")
        self.setCentralWidget(tabs)

        # recapito thread-safe di log ed eventi dal worker al thread GUI
        self._log_sig.connect(self.txt_log.append)
        self._flow_sig.connect(self._on_flow_event_impl)

        self._log("Kanga Sideload avviato.")
        if not device_mod.usbmux_available():
            self._log("⚠ Servizio Apple Mobile Device non trovato: installa iTunes "
                      "(versione classica) o l'app 'Apple Devices' dal Microsoft Store "
                      "per comunicare con l'iPhone.")
        session = self.flow.restore_session()
        if session and session.xcode_token:
            self._log("Sessione Apple presente: puoi firmare e installare direttamente.")
            self.btn_login.setText("Esci / cambia account")
            self.btn_install.setEnabled(True)

    def _build_sideload_tab(self) -> QWidget:
        central = QWidget()
        layout = QVBoxLayout(central)

        # --- riga IPA ---
        row_ipa = QHBoxLayout()
        self.txt_ipa = QLineEdit()
        self.txt_ipa.setReadOnly(True)
        self.txt_ipa.setPlaceholderText("Trascina qui un file .ipa oppure clicca Sfoglia…")
        btn_browse = QPushButton("Sfoglia…")
        btn_browse.clicked.connect(self._browse)
        row_ipa.addWidget(self.txt_ipa, 1)
        row_ipa.addWidget(btn_browse)
        layout.addLayout(row_ipa)

        self.lbl_ipa_info = QLabel("")
        layout.addWidget(self.lbl_ipa_info)

        # --- riga device ---
        row_dev = QHBoxLayout()
        row_dev.addWidget(QLabel("Dispositivo:"))
        self.cmb_devices = QComboBox()
        self.cmb_devices.setMinimumWidth(320)
        btn_refresh = QPushButton("Aggiorna")
        btn_refresh.clicked.connect(self.refresh_devices)
        row_dev.addWidget(self.cmb_devices, 1)
        row_dev.addWidget(btn_refresh)
        layout.addLayout(row_dev)

        self.lbl_devices = QLabel("")
        layout.addWidget(self.lbl_devices)

        # --- riga nome custom + azione ---
        row_act = QHBoxLayout()
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Nome visualizzato dell'app (opzionale)")
        self.btn_install = QPushButton("Firma e installa")
        self.btn_install.setEnabled(False)
        self.btn_install.setMinimumHeight(36)
        self.btn_install.clicked.connect(self._start_install)
        row_act.addWidget(self.txt_name, 1)
        row_act.addWidget(self.btn_install)
        layout.addLayout(row_act)

        self.btn_login = QPushButton("Accedi con Apple ID")
        self.btn_login.clicked.connect(self._open_login)
        layout.addWidget(self.btn_login)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        layout.addWidget(self.txt_log, 1)

        # instrada i log di sistema (kanga.* a livello INFO, terze parti WARNING)
        # dentro il log della finestra, da qualunque thread
        bridge = _GuiLogBridge(self)
        bridge.line.connect(self.txt_log.append)
        gui_handler = _GuiLogHandler(bridge)
        logging.getLogger("kanga").addHandler(gui_handler)
        logging.getLogger("kanga").setLevel(logging.INFO)
        for name in ("pymobiledevice3", "requests", "urllib3", "anisette"):
            lg = logging.getLogger(name)
            lg.addHandler(gui_handler)
            lg.setLevel(logging.WARNING)

        return central

    # ------------------------------------------------------------- helpers
    def _log(self, msg: str):
        self._log_sig.emit(msg)

    def _on_flow_event(self, tag: str, msg: str):
        # chiamato da thread worker: emette via signal -> esegue nel thread GUI
        self._flow_sig.emit(tag, msg)

    def _on_flow_event_impl(self, tag: str, msg: str):
        if tag == "log":
            self._log(msg)
        elif tag == "progress":
            self.progress.setVisible(True)
            try:
                pct = int(msg.split("%")[0])
                self.progress.setValue(pct)
            except ValueError:
                self.progress.setRange(0, 0)  # indeterminata
            self._log(f"installazione: {msg}")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".ipa":
                self._set_ipa(path)
                e.acceptProposedAction()
                return

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona file IPA", "", "App iOS (*.ipa)")
        if path:
            self._set_ipa(Path(path))

    def _set_ipa(self, path: Path):
        self.ipa_path = path
        self.txt_ipa.setText(str(path))
        size = readable_size(path.stat().st_size)
        from ..core.signer import parse_bundle_id_from_ipa

        bid = parse_bundle_id_from_ipa(path)
        self.lbl_ipa_info.setText(f"{path.name} — {size}" + (f" — bundle: {bid}" if bid else ""))
        self.btn_install.setEnabled(bool(self.flow.restore_session()))

    # ------------------------------------------------------------- device
    def refresh_devices(self):
        self.lbl_devices.setText("Ricerca dispositivi…")

        def run():
            if not device_mod.usbmux_available():
                raise DeviceError(
                    "Servizio Apple Mobile Device assente: installa iTunes classico "
                    "o l'app 'Apple Devices' (Microsoft Store)."
                )
            return device_mod.list_devices()

        self._worker = start_worker(
            run,
            on_ok=self._on_devices,
            on_fail=lambda msg: self.lbl_devices.setText(f"⚠ {msg}"),
        )

    def _on_devices(self, devices):
        self._devices = devices
        self.cmb_devices.clear()
        for d in devices:
            label = f"{d.name} ({d.udid[:12]}…)" + ("" if d.trusted else " — NON ABINATO")
            self.cmb_devices.addItem(label, d.udid)
        self.lbl_devices.setText(
            f"{len(devices)} dispositivo/i." if devices else "Nessun dispositivo collegato."
        )

    # ------------------------------------------------------------- login
    def _open_login(self, auto: bool = False):
        session = self.flow.restore_session()
        if session:
            self.flow.logout()
            self.btn_login.setText("Accedi con Apple ID")
            self.btn_install.setEnabled(False)
            self._log("Logout effettuato.")
            return

        saved = store.load_credentials()
        if auto and not saved:
            self._log("[LOGIN] Nessuna credenziale salvata: serve il login manuale")
            return
        # il tentativo più recente di questa sessione ha la precedenza su quanto
        # salvato su disco (es. password appena corretta)
        prefill = self._last_login or saved

        dlg = LoginDialog(self, prefill=prefill, remember_checked=self._remember_choice)
        if self._last_login_error and not auto:
            dlg.status(f"Tentativo precedente non riuscito: {self._last_login_error}")
        login_flow = LoginFlow(self.flow, "", "")
        login_flow.log.connect(dlg.status)
        login_flow.log.connect(self._on_login_log)
        login_flow.need_2fa.connect(self._on_need_2fa)
        login_flow.finished_ok.connect(self._on_login_ok)
        login_flow.failed.connect(self._on_login_fail)

        self._login_dlg = dlg
        self._login_flow = login_flow
        self._login_cancelled = False

        if auto:
            if not dlg.apple_id or not dlg.password:
                self._log("[LOGIN] Credenziali salvate incomplete: apro il dialog manuale")
                return
            login_flow._apple_id = dlg.apple_id
            login_flow._password = dlg.password
            self._log(f"[LOGIN] Login automatico con credenziali salvate ({dlg.apple_id})")
            self._start_login_thread(dlg, login_flow)
            return

        if not dlg.exec():
            return
        self._remember_credentials(dlg)
        login_flow._apple_id = dlg.apple_id
        login_flow._password = dlg.password
        dlg.status("Login in corso…")
        self._log(f"[LOGIN] Avvio login per {dlg.apple_id}")
        self._start_login_thread(dlg, login_flow)

    def _remember_credentials(self, dlg) -> None:
        """Memorizza le credenziali appena inserite, PRIMA del login.

        Prima venivano salvate solo a login riuscito: se Apple rispondeva con un
        errore (es. 503) non restava nulla e ogni nuovo tentativo ripartiva da
        campi vuoti. Ora restano in memoria per tutta la sessione e, con
        «Ricorda» attivo, vengono salvate subito su disco (DPAPI).
        """
        self._last_login = (dlg.apple_id, dlg.password)
        self._remember_choice = dlg.remember
        if dlg.remember:
            try:
                store.save_credentials(dlg.apple_id, dlg.password)
                self._log("Credenziali salvate su questo computer (cifrate DPAPI).")
            except Exception as e:
                self._log(f"⚠ Salvataggio credenziali fallito: {e}")
        else:
            store.clear_credentials()

    def _start_login_thread(self, dlg, login_flow):
        from PySide6.QtCore import QThread

        t = QThread(self)
        login_flow.moveToThread(t)
        t.started.connect(login_flow.run)
        # chiude il thread a login finito: altrimenti ogni tentativo lascia un
        # thread vivo e Qt va in crash alla chiusura dell'app
        login_flow.finished_ok.connect(t.quit)
        login_flow.failed.connect(t.quit)
        t.finished.connect(login_flow.deleteLater)
        t.start()
        self._login_thread = t

    def _on_login_log(self, msg: str):
        self._log(msg)

    def _on_need_2fa(self):
        dlg = self._login_dlg
        dlg.show_2fa()
        dlg.exec()
        code = dlg.code()
        if code:
            self._login_flow.provide_code(code)
        else:
            # dialog chiuso senza codice: annulla subito invece di attendere 3 minuti
            self._login_cancelled = True
            self._login_flow.cancel()

    def _on_login_ok(self, session):
        dlg = self._login_dlg
        # salva o cancella le credenziali secondo la checkbox
        if dlg.remember and dlg.apple_id and dlg.password:
            try:
                store.save_credentials(dlg.apple_id, dlg.password)
                self._log("Credenziali salvate su questo computer (cifrate DPAPI).")
            except Exception as e:
                self._log(f"⚠ Salvataggio credenziali fallito: {e}")
        elif not dlg.remember:
            store.clear_credentials()
        self._last_login_error = ""
        dlg.accept()
        self.btn_login.setText("Esci / cambia account")
        self.btn_install.setEnabled(True)
        self._log("Sessione pronta: firma e installazione abilitate.")
        self.session_ready.emit()

        # se il login era partito per riprendere un sideload, riprendilo ora
        if self._pending_install:
            self._pending_install = False
            self._log("[INSTALL] Sessione pronta: riprendo il sideload…")
            self._start_install()

    def _on_login_fail(self, msg):
        dlg = self._login_dlg
        dlg.reset_for_retry()
        self._pending_install = False
        if self._login_cancelled:
            self._log("[LOGIN] Annullato")
            return
        dlg.status(f"Errore: {msg}")
        self._log(f"[LOGIN] ✗ Fallito: {msg}")
        self._last_login_error = msg
        if dlg.apple_id and dlg.password and self._last_login is None:
            # login automatico con credenziali salvate: tienile per il nuovo tentativo
            self._last_login = (dlg.apple_id, dlg.password)
        # riapre subito il dialog con Apple ID e password già inseriti e l'errore
        # in evidenza: per riprovare basta premere «Accedi» (o correggere la password)
        QTimer.singleShot(0, self._reopen_login_after_failure)

    def _reopen_login_after_failure(self):
        if self.flow.restore_session():
            return  # nel frattempo una sessione è comparsa: non serve riprovare
        self._open_login()

    # ------------------------------------------------------------- install
    def _start_install(self):
        if not self.ipa_path:
            return
        session = self.flow.restore_session()
        if not session or not session.xcode_token:
            # nessuna sessione: se ci sono credenziali salvate fa il login
            # automatico e riprende l'installazione al termine
            if store.load_credentials():
                self._log("[INSTALL] Sessione scaduta: login automatico con credenziali salvate…")
                self._pending_install = True
                self.btn_install.setEnabled(False)
                self._open_login(auto=True)
            else:
                QMessageBox.warning(self, "Sessione assente", "Accedi prima con il tuo Apple ID.")
            return
        udid = self.cmb_devices.currentData()
        if not udid:
            QMessageBox.warning(self, "Nessun dispositivo", "Collega l'iPhone e clicca Aggiorna.")
            return

        self.btn_install.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        def run():
            return self.flow.sideload(
                session,
                self.ipa_path,
                udid,
                custom_name=self.txt_name.text().strip() or None,
            )

        def ok(signed_ipa):
            self.progress.setValue(100)
            self.progress.setVisible(False)
            self.btn_install.setEnabled(True)
            self._log(f"✅ Fatto: {signed_ipa}")

        def fail(msg):
            self.progress.setVisible(False)
            self.btn_install.setEnabled(True)
            friendly = msg
            if "503" in msg or "-80045" in msg or "-80046" in msg:
                friendly = (
                    "Apple ha bloccato il login con questo tipo di anisette "
                    "(problema noto dell'ecosistema da ottobre 2025). "
                    "Riprova più tardi o usa una sessione anisette differente."
                )
            self._log(f"⚠ Errore: {friendly}")
            QMessageBox.critical(self, "Errore", friendly)

        self._worker = start_worker(run, on_ok=ok, on_fail=fail)
