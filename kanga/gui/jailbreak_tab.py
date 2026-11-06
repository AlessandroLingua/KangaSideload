"""Tab Jailbreak: rileva il dispositivo, mostra i tool compatibili per
modello × iOS e installa gli IPA dalle release GitHub ufficiali usando la
normale pipeline di firma/instalazione (login Apple + zsign + device).
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import device as device_mod
from ..core import downloads
from ..core.devices_catalog import lookup as model_lookup
from ..core.jailbreak_catalog import Compatibility, compatible_tools
from ..core.jailbreak_catalog import find_tool
from .workers import start_worker

logger = logging.getLogger(__name__)


class JailbreakTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self._main = main_window
        self._device = None  # DeviceInfo corrente
        self._compat: list[Compatibility] = []
        self._download_worker = None

        layout = QVBoxLayout(self)

        # --- pannello dispositivo ---
        dev_frame = QFrame()
        dev_frame.setFrameShape(QFrame.StyledPanel)
        dev_layout = QVBoxLayout(dev_frame)
        self.lbl_device = QLabel("Nessun dispositivo analizzato — premi «Rileva dispositivo»")
        self.lbl_device.setWordWrap(True)
        self.lbl_device.setStyleSheet("font-size: 13px; font-weight: bold;")
        dev_layout.addWidget(self.lbl_device)
        self.lbl_model = QLabel("")
        self.lbl_model.setWordWrap(True)
        dev_layout.addWidget(self.lbl_model)
        btn_row = QHBoxLayout()
        self.btn_detect = QPushButton("Rileva dispositivo")
        self.btn_detect.clicked.connect(self.detect_device)
        btn_row.addWidget(self.btn_detect)
        btn_row.addStretch(1)
        dev_layout.addLayout(btn_row)
        layout.addWidget(dev_frame)

        # --- tabella tool ---
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Tool", "Compatibilità", "Descrizione", "Azione"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setMinimumHeight(220)
        layout.addWidget(self.table)

        # --- download bar ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # --- log condiviso con la finestra principale ---
        layout.addWidget(QLabel("Log operazioni (condiviso con il tab Sideload):"))
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(140)
        layout.addWidget(self.txt_log)

        warn = QLabel(
            "⚠ Prima di un jailbreak fai un backup. I jailbreak agiscono sul sistema: "
            "procedi solo su dispositivi tuoi. palera1n (checkm8) richiede la procedura "
            "DFU dal PC e non è installabile via sideload."
        )
        warn.setWordWrap(True)
        layout.addWidget(warn)

    # ----------------------------------------------------------- device
    def detect_device(self):
        self.lbl_device.setText("Ricerca dispositivo…")
        self._main._worker = start_worker(
            device_mod.list_devices,
            on_ok=self._on_devices,
            on_fail=lambda m: self.lbl_device.setText(f"⚠ {m}"),
        )

    def _on_devices(self, devices):
        if not devices:
            self.lbl_device.setText("Nessun dispositivo collegato via USB.")
            return
        d = devices[0]
        model = model_lookup(d.product_type or "")
        self._device = d
        if model:
            self.lbl_device.setText(f"{d.name} — iOS {d.product_version or '?'} (udid {d.udid[:12]}…)")
            self.lbl_model.setText(
                f"Modello: {model.name} | Chip: {model.chip} | Arch: {model.arch}"
                + (" | checkm8: sì (palera1n possibile via DFU)" if model.checkm8 else "")
                + ("" if d.trusted else " — DEVICE NON ABINATO: sbloccalo e premi Autorizza")
            )
        else:
            self.lbl_device.setText(f"{d.name} — iOS {d.product_version or '?'}")
            self.lbl_model.setText(f"Modello non nel catalogo: {d.product_type}")
        self._fill_tools(model, d.product_version or "")

    def _fill_tools(self, model, ios_version: str):
        if not model or not ios_version:
            self.lbl_model.setText(self.lbl_model.text() + " — versione iOS non leggibile")
            return
        self._compat = compatible_tools(model, ios_version)
        self.table.setRowCount(len(self._compat))
        for row, c in enumerate(self._compat):
            name_item = QTableWidgetItem(f"{c.tool.name}")
            if c.compatible:
                name_item.setForeground(Qt.green)
            self.table.setItem(row, 0, name_item)
            status = "✅ COMPATIBILE" if c.compatible else "— non compatibile"
            self.table.setItem(row, 1, QTableWidgetItem(f"{status} ({c.reason})"))
            self.table.setItem(row, 2, QTableWidgetItem(c.tool.description))
            if c.compatible and c.method == "sideload":
                btn = QPushButton("Scarica e installa")
                btn.clicked.connect(lambda _=False, tid=c.tool.id: self._install_tool(tid))
                self.table.setCellWidget(row, 3, btn)
            elif c.method == "dfu":
                btn_info = QPushButton("Guida")
                btn_info.clicked.connect(lambda _=False, tid=c.tool.id: self._show_dfu_guide(tid))
                self.table.setCellWidget(row, 3, btn_info)
            else:
                self.table.setCellWidget(row, 3, None)

    # ----------------------------------------------------------- install
    def _install_tool(self, tool_id: str):
        tool = find_tool(tool_id)
        if not tool or not self._device:
            return
        session = self._main.flow.restore_session()
        if not session or not session.xcode_token:
            QMessageBox.warning(
                self,
                "Login richiesto",
                "Per firmare e installare serve prima l'accesso con Apple ID (tab Sideload → Accedi).",
            )
            return
        udid = self._device.udid
        self._log(f"[JB] Scarico {tool.name} dal repo ufficiale {tool.repo}...")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        def on_dl(pct, name):
            self.progress.setRange(0, 100)
            self.progress.setValue(pct)

        def run():
            ipa = downloads.download_ipa(tool.repo, progress_cb=on_dl)
            self._log(f"[JB] Download completato: {ipa.name}")
            self._log(f"[JB] Firma e installazione su {udid}...")
            return self._main.flow.sideload(session, Path(ipa), udid)

        def ok(signed_ipa):
            self.progress.setVisible(False)
            self._log(f"[JB] ✅ {tool.name} installato! Aprilo dal home screen.")
            if tool.id in ("dopamine",):
                self._log("[JB] Nota: al riavvio ri-apri l'app per ri-applicare il jailbreak.")

        def fail(msg):
            self.progress.setVisible(False)
            self._log(f"[JB] ⚠ Errore: {msg}")
            QMessageBox.critical(self, "Errore jailbreak", msg)

        self._download_worker = start_worker(run, on_ok=ok, on_fail=fail)

    def _show_dfu_guide(self, tool_id: str):
        QMessageBox.information(
            self,
            "palera1n — procedura DFU",
            "I jailbreak checkm8 (A8-A11) NON passano dal sideload:\n\n"
            "1. Scarica palera1n dal sito ufficiale palera1n.github.io\n"
            "2. Metti il dispositivo in modalità DFU (con la guida del tool)\n"
            "3. Lancia palera1n dal PC (semi-tethered o rootless)\n\n"
            "Questa app non esegue exploit DFU: mostra solo la compatibilità.",
        )

    def _log(self, msg: str):
        self.txt_log.append(msg)
        self._main._log(msg)
