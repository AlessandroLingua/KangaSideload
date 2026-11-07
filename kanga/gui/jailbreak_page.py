"""Sezione Jailbreak: strumenti compatibili con il dispositivo selezionato.

Mostra la matrice modello × iOS (vedi core/jailbreak_catalog.py), scarica gli
IPA solo dalle fonti ufficiali dei progetti (release GitHub o sito ufficiale)
e li passa alla normale pipeline «Firma e installa» della finestra principale.
Nessun exploit è incluso: i tool sono app dei rispettivi autori.
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..core import downloads
from ..core.device import DeviceInfo
from ..core.devices_catalog import lookup as model_lookup
from ..core.errors import friendly_error
from ..core.jailbreak_catalog import Compatibility, JailbreakTool, compatible_tools
from . import icons, theme
from .widgets import Banner, Card, button, discard, icon_label, label, pill

# tool che vanno riaperti dopo ogni riavvio per riattivare il jailbreak
_SEMI_UNTETHERED = {"dopamine", "phoenix", "h3lix", "taurine", "unc0ver"}

DFU_GUIDE = (
    "I jailbreak checkm8 (chip A8-A11) non si installano come app:\n\n"
    "1. Scarica palera1n dal sito ufficiale palera1n.github.io\n"
    "2. Metti il dispositivo in modalità DFU seguendo la guida del tool\n"
    "3. Avvia palera1n dal computer (rootless o semi-tethered)\n\n"
    "Kanga non esegue exploit DFU: mostra solo la compatibilità."
)


class _ToolRow(QFrame):
    """Una riga della lista: nome, compatibilità, descrizione e azioni."""

    def __init__(self, compat: Compatibility, page: "JailbreakPage"):
        super().__init__()
        self.compat = compat
        tool = compat.tool
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(tool.name, "title"))
        if compat.compatible:
            head.addWidget(pill("Compatibile", "ok"))
        elif compat.method == "dfu" and compat.reason.startswith("iOS ") and "checkm8" not in compat.reason:
            head.addWidget(pill("Solo da PC (DFU)", "info"))
        else:
            head.addWidget(pill("Non compatibile", "neutral"))
        head.addStretch(1)
        col.addLayout(head)
        col.addWidget(label(tool.description, "muted", wrap=True))
        detail = compat.reason + (f" · {tool.notes}" if tool.notes and compat.compatible else "")
        col.addWidget(label(detail, "small", wrap=True))
        lay.addLayout(col, 1)

        self.buttons: list = []
        if compat.compatible and compat.method == "sideload":
            if tool.repo or tool.direct_ipa_url:
                b = button("Scarica e installa", "primary", icon_name="download",
                           tooltip="Scarica l'IPA dalla fonte ufficiale, poi firmalo e installalo")
                b.clicked.connect(lambda: page.download_and_install(tool))
                self.buttons.append(b)
            else:
                b_site = button("Sito ufficiale", icon_name="external", tooltip=tool.official_site)
                b_site.clicked.connect(lambda: page.open_site(tool.official_site))
                b_ipa = button("Installa IPA…", "primary", icon_name="package",
                               tooltip="Scegli l'IPA scaricato dal sito ufficiale")
                b_ipa.clicked.connect(lambda: page.choose_ipa(tool))
                self.buttons += [b_site, b_ipa]
        elif compat.method == "dfu":
            b = button("Guida", icon_name="info")
            b.clicked.connect(page.show_dfu_guide)
            self.buttons.append(b)
        for b in self.buttons:
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)


class JailbreakPage(QWidget):
    """Pagina della finestra principale (sezione «Jailbreak»)."""

    installRequested = Signal(object)  # Path dell'IPA da firmare e installare
    logMessage = Signal(str)
    _download_progress = Signal(int)   # emesso dal thread di download

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ScrollContent")
        self._device: DeviceInfo | None = None
        self._device_key: tuple | None = None
        self._busy = False
        self._downloading = False
        self.rows: list[_ToolRow] = []
        self._download_progress.connect(self._on_download_progress)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(12)
        v.addWidget(label("Jailbreak e strumenti", "h2"))
        v.addWidget(label(
            "Kanga mostra gli strumenti compatibili con il dispositivo selezionato e li scarica "
            "solo dalle fonti ufficiali dei progetti, poi li firma e installa come qualsiasi app.",
            "muted", wrap=True))

        # --- dispositivo
        self.card_device = Card("Dispositivo")
        row = QHBoxLayout()
        row.setSpacing(12)
        self.device_icon = QLabel()
        self.device_icon.setFixedSize(40, 40)
        row.addWidget(self.device_icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.lbl_device = label("", "title")
        self.lbl_device_sub = label("", "muted", wrap=True)
        col.addWidget(self.lbl_device)
        col.addWidget(self.lbl_device_sub)
        row.addLayout(col, 1)
        self.card_device.body.addLayout(row)
        v.addWidget(self.card_device)

        # --- strumenti
        self.card_tools = Card("Strumenti")
        self.tools_box = QVBoxLayout()
        self.tools_box.setSpacing(4)
        self.card_tools.body.addLayout(self.tools_box)
        self.lbl_tools_empty = label("", "muted", wrap=True)
        self.card_tools.body.addWidget(self.lbl_tools_empty)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.card_tools.body.addWidget(self.progress)
        self.lbl_progress = label("", "muted", wrap=True)
        self.lbl_progress.hide()
        self.card_tools.body.addWidget(self.lbl_progress)
        v.addWidget(self.card_tools)

        self.banner = Banner()
        v.addWidget(self.banner)

        warn = QFrame()
        warn.setProperty("banner", "warning")
        wl = QHBoxLayout(warn)
        wl.setContentsMargins(12, 10, 12, 10)
        wl.setSpacing(10)
        wl.addWidget(icon_label("alert", theme.current().warning, 18), 0, Qt.AlignmentFlag.AlignTop)
        wl.addWidget(label(
            "Prima di un jailbreak fai un backup del dispositivo e procedi solo su dispositivi tuoi. "
            "Con un Apple ID gratuito anche questi strumenti scadono dopo 7 giorni: reinstallali "
            "dalla sezione Recenti.", wrap=True), 1)
        v.addWidget(warn)
        v.addStretch(1)

        self._refresh()

    # ------------------------------------------------------------ stato
    def set_device(self, device: DeviceInfo | None) -> None:
        key = (device.udid, device.product_type, device.product_version, device.trusted) if device else None
        if key == self._device_key:
            return
        self._device_key = key
        self._device = device
        self._refresh()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        enabled = not (self._busy or self._downloading)
        for r in self.rows:
            for b in r.buttons:
                b.setEnabled(enabled)

    def _refresh(self) -> None:
        t = theme.current()
        for r in self.rows:
            self.tools_box.removeWidget(r)
            discard(r)
        self.rows = []
        d = self._device
        model = model_lookup(d.product_type) if d and d.product_type else None

        if d is None:
            self.device_icon.setPixmap(icons.icon_tile("phone", 40, t.surface_alt, t.muted))
            self.lbl_device.setText("Nessun dispositivo")
            self.lbl_device_sub.setText("Collega e seleziona un dispositivo nella sezione Installa.")
            self._show_empty("Collega un iPhone, iPad o iPod touch per vedere quali strumenti "
                             "sono compatibili con il suo modello e la sua versione di iOS.")
            return

        self.device_icon.setPixmap(icons.icon_tile("phone", 40, t.accent_soft, t.accent))
        self.lbl_device.setText(model.name if model else (d.name or d.product_type or d.udid))
        bits = [d.name if model else "", f"iOS {d.product_version}" if d.product_version else "",
                f"chip {model.chip} ({model.arch})" if model else d.product_type]
        sub = " · ".join(b for b in bits if b)
        if not d.trusted:
            sub += " — autorizza questo computer sul dispositivo per leggerne i dati"
        self.lbl_device_sub.setText(sub)

        if not d.product_version:
            self._show_empty("Versione di iOS non leggibile: sblocca il dispositivo e autorizza "
                             "questo computer (sezione Installa → Abbina).")
            return
        if model is None:
            self._show_empty(f"Il modello {d.product_type or '?'} non è nel catalogo "
                             "(kanga/core/devices_catalog.py): impossibile calcolare la compatibilità. "
                             "Firma e installazione di IPA funzionano comunque.")
            return

        compat = sorted(compatible_tools(model, d.product_version), key=lambda c: not c.compatible)
        for c in compat:
            r = _ToolRow(c, self)
            self.tools_box.addWidget(r)
            self.rows.append(r)
        n_ok = sum(c.compatible for c in compat)
        self.lbl_tools_empty.setText(
            "" if n_ok else f"Nessuno strumento installabile per {model.name} con iOS {d.product_version}.")
        self.lbl_tools_empty.setVisible(not n_ok)
        self._update_buttons()

    def _show_empty(self, text: str) -> None:
        self.lbl_tools_empty.setText(text)
        self.lbl_tools_empty.show()

    # ------------------------------------------------------------ azioni
    def download_and_install(self, tool: JailbreakTool) -> None:
        if self._busy or self._downloading:
            return
        from .workers import start_worker

        self.banner.hide()
        self._set_downloading(True, f"Download di {tool.name} dalla fonte ufficiale…")
        self.logMessage.emit(f"[Jailbreak] Download di {tool.name} dalla fonte ufficiale…")
        progress = self._download_progress

        def run():
            return downloads.fetch_tool_ipa(tool, progress_cb=lambda pct, _name: progress.emit(int(pct)))

        def ok(path):
            self._set_downloading(False)
            self.logMessage.emit(f"[Jailbreak] Download completato: {Path(path).name}")
            if tool.id in _SEMI_UNTETHERED:
                self.logMessage.emit(f"[Jailbreak] Nota: dopo ogni riavvio riapri {tool.name} "
                                     "per riattivare il jailbreak.")
            self.installRequested.emit(Path(path))

        def fail(exc):
            self._set_downloading(False)
            fe = friendly_error(exc)
            self.logMessage.emit(f"⚠ [Jailbreak] Download di {tool.name} non riuscito: {fe.detail}")
            self.banner.show_message(
                "error", f"Download di {tool.name} non riuscito", fe.message,
                f"Puoi scaricarlo dal sito ufficiale ({tool.official_site}) e usare «Installa IPA…»."
                if tool.official_site else fe.hint)

        start_worker(run, on_ok=ok, on_fail=fail)

    def choose_ipa(self, tool: JailbreakTool) -> None:
        if self._busy or self._downloading:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Scegli l'IPA di {tool.name}", str(Path.home()), "App iOS (*.ipa)")
        if path:
            self.logMessage.emit(f"[Jailbreak] Installazione manuale di {tool.name} da {path}")
            self.installRequested.emit(Path(path))

    def open_site(self, url: str) -> None:
        if url:
            webbrowser.open(url)
            self.logMessage.emit(f"[Jailbreak] Aperto nel browser: {url}")

    def show_dfu_guide(self) -> None:
        QMessageBox.information(self, "palera1n — procedura DFU", DFU_GUIDE)

    # ------------------------------------------------------------ download
    def _set_downloading(self, on: bool, text: str = "") -> None:
        self._downloading = on
        self.progress.setRange(0, 0 if on else 100)  # indeterminata finché non si conosce la dimensione
        self.progress.setValue(0)
        self.progress.setVisible(on)
        self.lbl_progress.setText(text)
        self.lbl_progress.setVisible(on and bool(text))
        self._update_buttons()

    def _on_download_progress(self, pct: int) -> None:
        if not self._downloading:
            return
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, pct)))
