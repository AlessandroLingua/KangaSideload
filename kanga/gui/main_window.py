"""Finestra principale di Kanga Sideload."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
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
    QTextEdit,
    QVBoxLayout,
)

from ..core import device as device_mod
from ..core import store
from ..core.appflow import AppFlow, AppFlowError
from ..core.device import DeviceError
from ..core.gsa import GsaError
from ..core.signer import ZsignError, readable_size
from .login_dialog import LoginDialog
from .workers import GenericWorker, LoginFlow, start_worker

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kanga Sideload")
        self.resize(760, 560)
        self.setAcceptDrops(True)

        self.ipa_path: Path | None = None
        self.flow = AppFlow(on_event=self._on_flow_event)
        self._worker: GenericWorker | None = None
        self._login_flow: LoginFlow | None = None
        self._devices: list = []

        central = QFrame()
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

        self.setCentralWidget(central)
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

    # ------------------------------------------------------------- helpers
    def _log(self, msg: str):
        self.txt_log.append(msg)
        logger.info(msg)

    def _on_flow_event(self, tag: str, msg: str):
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
    def _open_login(self):
        session = self.flow.restore_session()
        if session:
            self.flow.logout()
            self.btn_login.setText("Accedi con Apple ID")
            self.btn_install.setEnabled(False)
            self._log("Logout effettuato.")
            return

        dlg = LoginDialog(self)
        login_flow = LoginFlow(self.flow, "", "")
        login_flow.log.connect(lambda m: dlg.status(m))
        login_flow.need_2fa.connect(lambda: (dlg.show_2fa(), dlg.exec()))

        def on_ok(session):
            dlg.accept()
            self.btn_login.setText("Esci / cambia account")
            self.btn_install.setEnabled(True)
            self._log("Sessione pronta: firma e installazione abilitate.")

        def on_fail(msg):
            dlg.status(f"Errore: {msg}")
            dlg.reset_for_retry()

        login_flow.finished_ok.connect(on_ok)
        login_flow.failed.connect(on_fail)

        while True:
            if not dlg.exec():
                return
            login_flow._apple_id = dlg.apple_id
            login_flow._password = dlg.password
            dlg.status("Login in corso…")
            self._login_flow = login_flow
            from PySide6.QtCore import QThread

            t = QThread(self)
            login_flow.moveToThread(t)
            t.started.connect(login_flow.run)
            t.start()
            self._login_thread = t
            if dlg.result() and dlg.code():
                # il codice 2FA è stato inserito: passa al worker il codice e continua
                login_flow.provide_code(dlg.code())
            break

    # ------------------------------------------------------------- install
    def _start_install(self):
        if not self.ipa_path:
            return
        session = self.flow.restore_session()
        if not session or not session.xcode_token:
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
