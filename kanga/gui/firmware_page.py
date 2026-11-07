"""Sezione Firmware: qualsiasi modello Apple e qualsiasi versione di iOS.

- Modello: quello del dispositivo collegato oppure uno qualsiasi scelto
  dall'elenco (iPhone, iPad, iPod touch), anche senza nulla di collegato.
- Versioni: tutte quelle pubblicate da Apple per il modello, con lo stato di
  firma attuale. Ogni IPSW si può scaricare dai server Apple (anche quelli non
  firmati, es. per futurerestore con SHSH salvati).
- Installazione (downgrade/ripristino) e SHSH: solo per le versioni che Apple
  firma ancora e con il dispositivo collegato via cavo, perché ogni ripristino
  deve essere autorizzato in tempo reale dai server di Apple (vedi core/firmware.py).

Il ripristino si fa con iTunes (consigliato su Windows: Kanga apre iTunes e
mostra il file) oppure direttamente da Kanga (pymobiledevice3), se c'è libusb.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import firmware as firmware_mod
from ..core.device import DeviceInfo
from ..core.devices_catalog import lookup as model_lookup
from ..core.errors import friendly_error
from ..core.signer import readable_size
from . import icons, theme
from .widgets import Banner, Card, button, discard, icon_label, label, pill

UNSIGNED_SHOWN = 8  # versioni non firmate mostrate prima di «Mostra tutte»

RELATION_PILL = {
    firmware_mod.CURRENT: ("Installata", "info"),
    firmware_mod.DOWNGRADE: ("Downgrade", "warn"),
    firmware_mod.UPGRADE: ("Aggiornamento", "neutral"),
}

ITUNES_STEPS = (
    "1. In iTunes fai clic sull'icona dell'iPhone in alto a sinistra (scheda Riepilogo).\n"
    "2. Tieni premuto Maiusc e fai clic su «Ripristina iPhone…».\n"
    "3. Scegli il file «{name}» (Kanga l'ha appena mostrato in Esplora file).\n"
    "iTunes chiede ad Apple la firma: se la versione non è più firmata si ferma prima di toccare l'iPhone."
)

UNSIGNED_TOOLTIP = (
    "Apple non firma più questa versione: nessun programma può installarla con un ripristino "
    "normale. Puoi scaricarla (es. per futurerestore, che richiede SHSH salvati quando era firmata)."
)


class _LogForwarder(logging.Handler):
    """Porta nel Registro i messaggi di pymobiledevice3 durante il ripristino (da qualunque thread)."""

    def __init__(self, signal):
        super().__init__(logging.INFO)
        self._signal = signal

    def emit(self, record):
        try:
            self._signal.emit(f"  {record.getMessage()}")
        except Exception:
            pass


class _FirmwareRow(QFrame):
    def __init__(self, fw: firmware_mod.Firmware, rel: str | None, local: Path | None,
                 shsh: Path | None, device_ok: bool, page: "FirmwarePage"):
        super().__init__()
        self.fw = fw
        self.relation = rel
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 5, 0, 5)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(f"iOS {fw.version}", "title"))
        if fw.signed:
            head.addWidget(pill("Firmata da Apple", "ok"))
        else:
            unsigned = pill("Non firmata", "neutral")
            unsigned.setToolTip(UNSIGNED_TOOLTIP)
            head.addWidget(unsigned)
        if rel is not None and (rel == firmware_mod.CURRENT or fw.signed):
            text, kind = RELATION_PILL[rel]
            head.addWidget(pill(text, kind))
        if shsh is not None:
            saved = pill("SHSH salvati", "info")
            saved.setToolTip(str(shsh))
            head.addWidget(saved)
        head.addStretch(1)
        col.addLayout(head)
        bits = [f"build {fw.buildid}", readable_size(fw.size) if fw.size else "",
                f"uscita il {fw.released}" if fw.released else "", "già scaricato" if local else ""]
        col.addWidget(label(" · ".join(b for b in bits if b), "small", wrap=True))
        lay.addLayout(col, 1)

        self.buttons: list = []
        self.needs_usb: list = []  # pulsanti che richiedono il dispositivo via cavo
        installable = fw.signed and device_ok
        if installable:
            b_shsh = button("Salva SHSH", icon_name="shield",
                            tooltip="Salva i ticket di firma di Apple per questa versione (per downgrade futuri)")
            b_shsh.clicked.connect(lambda: page.save_shsh(fw))
            self.buttons.append(b_shsh)
            self.needs_usb.append(b_shsh)
        if local and installable:
            b = button("Ripristina…" if rel != firmware_mod.UPGRADE else "Installa…", "primary",
                       icon_name="refresh", tooltip=str(local))
            b.clicked.connect(lambda: page.restore(fw, local))
            self.needs_usb.append(b)
        elif local:
            b = button("Mostra file", icon_name="folder", tooltip=str(local))
            b.clicked.connect(lambda: _reveal(local))
        else:
            b = button("Scarica IPSW", "primary" if rel == firmware_mod.DOWNGRADE and fw.signed else None,
                       icon_name="download", tooltip=f"Scarica {fw.filename} dai server Apple")
            b.clicked.connect(lambda: page.download(fw))
        self.buttons.append(b)
        if local and device_ok and not fw.signed:
            b_fr = button("Ripristina con SHSH…", icon_name="refresh",
                          tooltip="Downgrade a questa versione non firmata con futurerestore e SHSH salvati")
            b_fr.clicked.connect(lambda: page.restore_with_shsh(fw, local))
            self.buttons.append(b_fr)
            self.needs_usb.append(b_fr)
        for b in self.buttons:
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)


class FirmwarePage(QWidget):
    """Pagina della finestra principale (sezione «Firmware»)."""

    logMessage = Signal(str)
    busyChanged = Signal(bool)          # True durante un ripristino: la finestra blocca il resto
    _progress = Signal(object, object)  # (scaricati, totale) dal thread di download

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self.setObjectName("ScrollContent")
        self.backend = backend or firmware_mod
        self._device: DeviceInfo | None = None
        self._device_key = None
        self.info: firmware_mod.DeviceFirmwareInfo | None = None
        self.firmwares: list[firmware_mod.Firmware] = []
        self.models: list[firmware_mod.Model] = []
        self.model: firmware_mod.Model | None = None  # None = modello del dispositivo collegato
        self.rows: list[_FirmwareRow] = []
        self._loaded_key = None
        self._loading = False
        self._models_loading = False
        self._show_all = False
        self._outer_busy = False
        self._restoring = False
        self._downloading: firmware_mod.Firmware | None = None
        self._stop = threading.Event()
        self._progress.connect(self._on_progress)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(12)
        v.addWidget(label("Firmware iOS", "h2"))
        v.addWidget(label(
            "Scegli qualsiasi iPhone, iPad o iPod touch e vedi tutte le versioni di iOS pubblicate da "
            "Apple: puoi scaricarle tutte. Installarne una (anche per un downgrade) è possibile solo "
            "se Apple la firma ancora: ogni ripristino deve essere autorizzato in tempo reale dai "
            "server Apple, per qualunque programma.", "muted", wrap=True))

        # --- modello / dispositivo
        self.card_device = Card("Modello")
        pick = QHBoxLayout()
        pick.setSpacing(8)
        self.cmb_model = QComboBox()
        self.cmb_model.setEditable(True)
        self.cmb_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cmb_model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cmb_model.setAccessibleName("Modello del dispositivo")
        self.cmb_model.lineEdit().setPlaceholderText("Cerca un modello (es. iPhone 12, iPad Air)…")
        self.cmb_model.addItem("Dispositivo collegato", "")
        completer = self.cmb_model.completer()
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.cmb_model.currentIndexChanged.connect(self._on_model_selected)
        pick.addWidget(self.cmb_model, 1)
        self.btn_reload = button("Aggiorna", icon_name="refresh", tooltip="Rileggi le versioni e lo stato di firma")
        self.btn_reload.clicked.connect(lambda: self.load(force=True))
        pick.addWidget(self.btn_reload)
        self.card_device.body.addLayout(pick)
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

        # --- riepilogo + versioni
        self.summary = Banner()
        self.summary.btn_close.hide()
        v.addWidget(self.summary)
        self.card_versions = Card("Versioni di iOS")
        self.versions_box = QVBoxLayout()
        self.versions_box.setSpacing(2)
        self.card_versions.body.addLayout(self.versions_box)
        self.lbl_empty = label("", "muted", wrap=True)
        self.card_versions.body.addWidget(self.lbl_empty)
        self.btn_all = button("", "link")
        self.btn_all.clicked.connect(self._toggle_all)
        self.btn_all.hide()
        self.card_versions.body.addWidget(self.btn_all, 0, Qt.AlignmentFlag.AlignLeft)
        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        prog.addWidget(self.progress, 1)
        self.btn_cancel = button("Annulla", tooltip="Interrompi: il download riprende da qui la prossima volta")
        self.btn_cancel.clicked.connect(self.cancel_download)
        prog.addWidget(self.btn_cancel)
        self.progress_row = QWidget()
        self.progress_row.setLayout(prog)
        self.progress_row.hide()
        self.card_versions.body.addWidget(self.progress_row)
        self.lbl_progress = label("", "muted", wrap=True)
        self.lbl_progress.hide()
        self.card_versions.body.addWidget(self.lbl_progress)
        v.addWidget(self.card_versions)

        self.banner = Banner()
        v.addWidget(self.banner)

        warn = QFrame()
        warn.setProperty("banner", "warning")
        wl = QHBoxLayout(warn)
        wl.setContentsMargins(12, 10, 12, 10)
        wl.setSpacing(10)
        wl.addWidget(icon_label("alert", theme.current().warning, 18), 0, Qt.AlignmentFlag.AlignTop)
        wl.addWidget(label(
            "Un ripristino cancella tutti i dati del dispositivo: fai prima un backup (iTunes → "
            "«Esegui backup adesso»). Il downgrade non può reinstallare un backup fatto con una "
            "versione di iOS più recente. Dopo il ripristino serve l'Apple ID per l'attivazione.",
            wrap=True), 1)
        v.addWidget(warn)
        v.addStretch(1)
        self._refresh_device()

    # ------------------------------------------------------------ stato
    @property
    def busy(self) -> bool:
        return self._restoring or self._downloading is not None

    def target_identifier(self) -> str:
        if self.model is not None:
            return self.model.identifier
        return self._device.product_type if self._device else ""

    def device_matches(self) -> bool:
        """Il dispositivo collegato è proprio il modello mostrato (serve per SHSH e ripristino)."""
        d = self._device
        return d is not None and bool(d.product_type) and d.product_type == self.target_identifier()

    def _key(self):
        return (self.target_identifier(), self._device_key if self.device_matches() else None)

    def set_device(self, device: DeviceInfo | None) -> None:
        key = (device.udid, device.product_type, device.product_version, device.connection) if device else None
        if key == self._device_key:
            return
        self._device_key = key
        self._device = device
        if not self._restoring:
            self.info = None
        self._refresh_device()
        if self._loaded_key != self._key():
            self.firmwares = [] if self.model is None else self.firmwares
            self._rebuild()
            if self.isVisible():
                self.load()
        else:
            self._rebuild()

    def set_busy(self, busy: bool) -> None:
        self._outer_busy = busy
        self._update_buttons()

    def activate(self) -> None:
        """La pagina è stata aperta: carica i modelli e le versioni se non sono già pronti."""
        self.load_models()
        self.load()

    def _usb(self) -> bool:
        return self._device is not None and not self._device.is_network and self._device.trusted

    def _update_buttons(self) -> None:
        blocked = self._outer_busy or self._restoring
        device_ready = self._usb() and self.info is not None and self.device_matches()
        for r in self.rows:
            for b in r.buttons:
                b.setEnabled(not blocked and self._downloading is None and (b not in r.needs_usb or device_ready))
        self.btn_reload.setEnabled(not blocked and not self._loading and bool(self.target_identifier()))
        self.cmb_model.setEnabled(not self._restoring)

    def _model_name(self, identifier: str) -> str:
        found = next((m.name for m in self.models if m.identifier == identifier), "")
        if found:
            return found
        local = model_lookup(identifier) if identifier else None
        return local.name if local else identifier

    def _refresh_device(self) -> None:
        t = theme.current()
        d = self._device
        self.cmb_model.setItemText(0, f"Dispositivo collegato ({self._model_name(d.product_type) or d.name})"
                                   if d and d.product_type else "Dispositivo collegato")
        target = self.target_identifier()
        if not target:
            self.device_icon.setPixmap(icons.icon_tile("phone", 40, t.surface_alt, t.muted))
            self.lbl_device.setText("Nessun dispositivo")
            self.lbl_device_sub.setText("Collega un dispositivo oppure scegli un modello qui sopra.")
            return
        matches = self.device_matches()
        self.device_icon.setPixmap(icons.icon_tile("phone", 40, t.accent_soft if matches else t.surface_alt,
                                                   t.accent if matches else t.muted))
        self.lbl_device.setText(self._model_name(target))
        if not matches:
            self.lbl_device_sub.setText(
                f"{target} · nessun dispositivo di questo modello collegato: puoi vedere e scaricare "
                "tutte le versioni; per SHSH e ripristino collegalo via cavo USB.")
            return
        info = self.info
        version = info.version if info else d.product_version
        bits = [d.name, f"iOS {version}" + (f" ({info.build})" if info and info.build else "") if version else "",
                target, f"ECID {info.ecid_hex}" if info and info.ecid else ""]
        sub = " · ".join(b for b in bits if b)
        if d.is_network:
            sub += " — collegato via Wi-Fi: per SHSH e ripristino serve il cavo USB"
        elif not d.trusted:
            sub += " — autorizza questo computer sul dispositivo (sezione Installa → Abbina)"
        self.lbl_device_sub.setText(sub)

    # ------------------------------------------------------------ modelli
    def load_models(self) -> None:
        if self.models or self._models_loading:
            return
        from .workers import start_worker

        self._models_loading = True

        def ok(models):
            self._models_loading = False
            self.models = list(models)
            self.cmb_model.blockSignals(True)
            for m in self.models:
                self.cmb_model.addItem(f"{m.name} — {m.identifier}", m.identifier)
            self.cmb_model.blockSignals(False)
            self._refresh_device()

        def fail(exc):
            self._models_loading = False
            self.logMessage.emit(f"⚠ [Firmware] {friendly_error(exc).detail}")

        start_worker(self.backend.list_models, on_ok=ok, on_fail=fail)

    def select_model(self, identifier: str) -> bool:
        idx = self.cmb_model.findData(identifier)
        if idx >= 0:
            self.cmb_model.setCurrentIndex(idx)
        return idx >= 0

    def _on_model_selected(self, index: int) -> None:
        identifier = self.cmb_model.itemData(index) or ""
        self.model = next((m for m in self.models if m.identifier == identifier), None) if identifier else None
        self._show_all = False
        self.firmwares = []
        self._refresh_device()
        self._rebuild()
        self.load(force=True)

    # ------------------------------------------------------------ elenco
    def load(self, force: bool = False) -> None:
        target = self.target_identifier()
        if not target or self._loading or self._restoring:
            self._update_buttons()
            return
        key = self._key()
        if not force and self._loaded_key == key:
            return
        from .workers import start_worker

        self._loading = True
        self.lbl_empty.setText(f"Lettura delle versioni di iOS per {self._model_name(target)}…")
        self.lbl_empty.show()
        self._update_buttons()
        backend, read_info = self.backend, self.device_matches() and self._usb()
        udid = self._device.udid if self._device else ""

        def run():
            info = None
            if read_info:
                try:
                    info = backend.read_device_info(udid)
                except Exception:
                    info = None  # l'elenco si mostra comunque
            return info, backend.list_firmwares(target)

        def ok(result):
            self._loading = False
            if key != self._key():
                self.load()  # nel frattempo è cambiato modello o dispositivo
                return
            self.info, self.firmwares = result
            self._loaded_key = key
            self._refresh_device()
            self._rebuild()

        def fail(exc):
            self._loading = False
            fe = friendly_error(exc)
            self.lbl_empty.setText(f"{fe.message} {fe.hint}".strip())
            self.logMessage.emit(f"⚠ [Firmware] {fe.detail}")
            self._update_buttons()

        start_worker(run, on_ok=ok, on_fail=fail)

    def current_version(self) -> tuple[str, str]:
        if not self.device_matches():
            return "", ""
        if self.info:
            return self.info.version, self.info.build
        return self._device.product_version, ""

    def _rebuild(self) -> None:
        for r in self.rows:
            self.versions_box.removeWidget(r)
            discard(r)
        self.rows = []
        self.summary.hide()
        if not self.firmwares:
            self.btn_all.hide()
            if not self.target_identifier():
                self.lbl_empty.setText("Collega un dispositivo o scegli un modello per vedere le versioni di iOS.")
            elif not self._loading:
                self.lbl_empty.setText("Apri questa sezione o premi «Aggiorna» per leggere le versioni.")
            self.lbl_empty.show()
            self._update_buttons()
            return
        self.lbl_empty.hide()
        version, build = self.current_version()
        device_ok = self.device_matches()
        ecid = self.info.ecid if self.info else 0
        signed = [f for f in self.firmwares if f.signed]
        unsigned = [f for f in self.firmwares if not f.signed]
        shown_unsigned = unsigned if self._show_all else unsigned[:UNSIGNED_SHOWN]
        for fw in signed + shown_unsigned:
            rel = self.backend.relation(fw, version, build) if version else None
            r = _FirmwareRow(fw, rel, self.backend.local_ipsw(fw), self.backend.local_shsh(fw, ecid),
                             device_ok, self)
            self.versions_box.addWidget(r)
            self.rows.append(r)
        hidden = len(unsigned) - len(shown_unsigned)
        self.btn_all.setText("Mostra meno versioni" if self._show_all
                             else f"Mostra tutte le versioni (+{hidden})")
        self.btn_all.setVisible(self._show_all or hidden > 0)
        self._show_summary(signed, version)
        self._update_buttons()

    def _show_summary(self, signed, version: str) -> None:
        names = ", ".join(f"iOS {f.version}" for f in signed) or "nessuna versione"
        name = self._model_name(self.target_identifier())
        total = len(self.firmwares)
        if not version:
            self.summary.show_message(
                "info", f"{name}: {total} versioni di iOS, firmata da Apple: {names}",
                "Tutte le versioni si possono scaricare; si installano solo quelle firmate.",
                "Per installare collega via USB un dispositivo di questo modello.")
        elif downgrades := [f for f in signed
                            if self.backend.relation(f, version) == firmware_mod.DOWNGRADE]:
            self.summary.show_message(
                "success", f"Downgrade possibile a iOS {downgrades[0].version}",
                f"Apple firma ancora: {names}. Approfittane presto: smetterà di firmarla tra pochi giorni.",
                "Scarica l'IPSW, salva gli SHSH e poi ripristina.")
        else:
            self.summary.show_message(
                "info", "Nessun downgrade possibile in questo momento",
                f"Apple firma solo {names}, mentre sul dispositivo c'è iOS {version}.",
                "Le versioni precedenti non sono più firmate: nessun programma può installarle con "
                "un ripristino normale. Salva gli SHSH delle versioni firmate: con futurerestore "
                "potrebbero servire per un downgrade futuro.")
        self.summary.btn_close.hide()

    def _toggle_all(self) -> None:
        self._show_all = not self._show_all
        self._rebuild()

    # ------------------------------------------------------------ download
    def download(self, fw: firmware_mod.Firmware) -> None:
        if self._downloading is not None or self._restoring:
            return
        from .workers import start_worker

        self._downloading = fw
        self._stop.clear()
        self.banner.hide()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress_row.show()
        self.lbl_progress.setText(f"Download di {fw.filename} dai server Apple…")
        self.lbl_progress.show()
        self._update_buttons()
        self.logMessage.emit(f"[Firmware] Download di {fw.label} per {fw.identifier}: {fw.url}")
        progress, stop = self._progress, self._stop

        def run():
            return self.backend.download_ipsw(fw, progress_cb=lambda d, t: progress.emit(d, t),
                                              should_stop=stop.is_set)

        def ok(path):
            self._end_download()
            self.logMessage.emit(f"[Firmware] IPSW pronto e verificato: {path}")
            hint = ("Ora puoi salvare gli SHSH e ripristinare." if fw.signed and self.device_matches()
                    else "" if fw.signed else "Versione non firmata da Apple: il file serve solo per "
                    "archivio o per futurerestore con SHSH salvati.")
            self.banner.show_message("success", f"{fw.label} scaricato e verificato", str(path), hint,
                                     actions=[("Mostra file", lambda: _reveal(path))])
            self._rebuild()

        def fail(exc):
            self._end_download()
            if isinstance(exc, firmware_mod.DownloadCancelled):
                self.banner.show_message("info", "Download in pausa",
                                         "Premi di nuovo «Scarica IPSW» per riprendere da dove eri rimasto.")
                return
            fe = friendly_error(exc)
            self.logMessage.emit(f"⚠ [Firmware] {fe.detail}")
            self.banner.show_message("error", "Download non riuscito", fe.message, fe.hint)

        start_worker(run, on_ok=ok, on_fail=fail)

    def cancel_download(self) -> None:
        self._stop.set()
        self.lbl_progress.setText("Interruzione del download…")

    def _end_download(self) -> None:
        self._downloading = None
        self.progress_row.hide()
        self.lbl_progress.hide()
        self._update_buttons()

    def _on_progress(self, done, total) -> None:
        if self._downloading is None:
            return
        if done < 0:
            self.progress.setRange(0, 0)
            self.lbl_progress.setText("Verifica dell'IPSW (SHA-256)…")
            return
        if total:
            self.progress.setValue(int(done * 1000 / total))
            self.lbl_progress.setText(f"Download: {readable_size(done)} di {readable_size(total)}")

    # ------------------------------------------------------------ SHSH
    def save_shsh(self, fw: firmware_mod.Firmware) -> None:
        if self.info is None or self._restoring or not self.device_matches():
            return
        from .workers import start_worker

        info = self.info
        self.logMessage.emit(f"[Firmware] Richiesta SHSH per {fw.label} (ECID {info.ecid_hex})…")

        def ok(path):
            self.logMessage.emit(f"[Firmware] SHSH salvati: {path}")
            self.banner.show_message(
                "success", f"SHSH di {fw.label} salvati", str(path),
                "Conservali: valgono per questo dispositivo e servono a strumenti come futurerestore.",
                actions=[("Apri cartella", lambda: _reveal(path))])
            self._rebuild()

        def fail(exc):
            fe = friendly_error(exc)
            self.logMessage.emit(f"⚠ [Firmware] {fe.detail}")
            self.banner.show_message("error", "SHSH non salvati", fe.message, fe.hint)

        start_worker(self.backend.save_shsh, info, fw, on_ok=ok, on_fail=fail)

    # ------------------------------------------------------------ ripristino
    def ask_restore_method(self, fw: firmware_mod.Firmware, direct_ok: bool) -> str:
        """Conferma e scelta del metodo: 'itunes' | 'direct' | ''."""
        d = self._device
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Ripristino del firmware")
        box.setText(f"Installare {fw.label} su {d.name or 'questo dispositivo'}?")
        box.setInformativeText(
            "Tutti i dati del dispositivo verranno CANCELLATI. Prima di continuare:\n"
            "• fai un backup con iTunes\n"
            "• disattiva «Trova il mio iPhone» (Impostazioni → il tuo nome → Dov'è)\n"
            "• tieni il dispositivo collegato via cavo fino alla fine\n\n"
            "Con iTunes: Kanga apre iTunes e mostra il file, tu confermi il ripristino.\n"
            "Direttamente: Kanga esegue il ripristino da solo"
            + ("." if direct_ok else " (non disponibile: serve libusb, vedi il Registro)."))
        b_itunes = box.addButton("Con iTunes (consigliato)", QMessageBox.ButtonRole.AcceptRole)
        b_direct = box.addButton("Direttamente con Kanga", QMessageBox.ButtonRole.DestructiveRole)
        b_direct.setEnabled(direct_ok)
        box.addButton("Annulla", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_itunes)
        box.exec()
        clicked = box.clickedButton()
        return "itunes" if clicked is b_itunes else "direct" if clicked is b_direct else ""

    def restore(self, fw: firmware_mod.Firmware, path: Path) -> None:
        if self.busy or self._outer_busy or not self._usb() or not self.device_matches() or not fw.signed:
            return
        direct_ok = self.backend.libusb_available()
        if not direct_ok:
            self.logMessage.emit(
                f"[Firmware] Ripristino diretto non disponibile: serve libusb-1.0.dll in "
                f"{self.backend.libusb_dir()} e il driver WinUSB per la modalità Recovery (es. con Zadig).")
        method = self.ask_restore_method(fw, direct_ok)
        if method == "itunes":
            self._restore_with_itunes(fw, Path(path))
        elif method == "direct":
            self._restore_direct(fw, Path(path))

    def _restore_with_itunes(self, fw: firmware_mod.Firmware, path: Path) -> None:
        _reveal(path)
        exe = self.backend.itunes_exe()
        if exe:
            _launch(exe)
        self.logMessage.emit(f"[Firmware] Ripristino con iTunes: {path}")
        self.banner.show_message(
            "info", f"Completa il ripristino di {fw.label} in iTunes",
            ITUNES_STEPS.format(name=path.name),
            "" if exe else "iTunes non trovato: installalo dal sito Apple (non dal Microsoft Store).",
            actions=[("Mostra file IPSW", lambda: _reveal(path))])

    def _restore_direct(self, fw: firmware_mod.Firmware, path: Path) -> None:
        from .workers import start_worker

        d = self._device
        self._restoring = True
        self.busyChanged.emit(True)
        self._update_buttons()
        self.banner.show_message("info", f"Ripristino di {fw.label} in corso",
                                 "Non scollegare il dispositivo: può richiedere 10-20 minuti e il "
                                 "dispositivo si riavvierà più volte.")
        self.logMessage.emit(f"— Ripristino di {fw.label} su {d.name or d.udid} —")
        forwarder = _LogForwarder(self.logMessage)
        pmd3 = logging.getLogger("pymobiledevice3")
        pmd3.addHandler(forwarder)

        def done():
            pmd3.removeHandler(forwarder)
            self._restoring = False
            self.busyChanged.emit(False)
            self._update_buttons()

        def ok(_):
            done()
            self.logMessage.emit(f"[Firmware] Ripristino di {fw.label} completato")
            self.banner.show_message("success", f"{fw.label} installato",
                                     "Il dispositivo si riavvia: completa la configurazione iniziale "
                                     "(serve l'Apple ID per l'attivazione).")

        def fail(exc):
            done()
            fe = friendly_error(exc)
            self.logMessage.emit(f"⚠ [Firmware] {fe.detail}")
            self.banner.show_message(
                "error", "Ripristino non riuscito", fe.message,
                "Se il dispositivo è rimasto in modalità Recovery, collegalo e ripristinalo con iTunes "
                "all'ultima versione firmata.")

        start_worker(self.backend.restore, d.udid, path, on_ok=ok, on_fail=fail)


    # ------------------------------------------------------------ futurerestore
    def restore_with_shsh(self, fw: firmware_mod.Firmware, path: Path) -> None:
        """Downgrade a una versione non firmata: legge il nonce, poi apre il dialog di futurerestore."""
        if self.busy or self._outer_busy or not self._usb() or not self.device_matches() or self.info is None:
            return
        from .workers import start_worker

        udid = self._device.udid
        self.logMessage.emit(f"[Firmware] Lettura del nonce di {self._device.name or udid}…")

        def read():
            try:
                return self.backend.read_ap_nonce(udid)
            except Exception:
                return None  # il dialog lo segnala come "non verificato"

        start_worker(read, on_ok=lambda nonce: self._open_futurerestore(fw, Path(path), nonce),
                     on_fail=lambda _e: self._open_futurerestore(fw, Path(path), None))

    def open_futurerestore_dialog(self, fw, path: Path, nonce):
        """Mostra il dialog; ritorna gli argomenti di futurerestore o None se annullato."""
        from .futurerestore_dialog import FuturerestoreDialog

        dlg = FuturerestoreDialog(self.backend, fw, self.info, path, nonce, self)
        if dlg.exec() == FuturerestoreDialog.DialogCode.Accepted:
            return dlg.args()
        return None

    def _open_futurerestore(self, fw, path: Path, nonce) -> None:
        if self.busy or self._outer_busy:
            return
        args = self.open_futurerestore_dialog(fw, path, nonce)
        if args:
            self._run_futurerestore(fw, args)

    def _run_futurerestore(self, fw: firmware_mod.Firmware, args: list[str]) -> None:
        from .workers import start_worker

        d = self._device
        exe = args[0]
        self._restoring = True
        self.busyChanged.emit(True)
        self._update_buttons()
        self.banner.show_message("info", f"Downgrade a {fw.label} con futurerestore in corso",
                                 "Non scollegare il dispositivo: può richiedere 10-20 minuti e il "
                                 "dispositivo si riavvierà più volte. L'avanzamento è nel Registro.")
        self.logMessage.emit(f"— Downgrade a {fw.label} su {d.name or d.udid} con futurerestore —")
        self.logMessage.emit("  " + " ".join(args))
        log = self.logMessage

        def done():
            self._restoring = False
            self.busyChanged.emit(False)
            self._update_buttons()

        def ok(_):
            done()
            self.logMessage.emit(f"[Firmware] Downgrade a {fw.label} completato")
            self.banner.show_message("success", f"{fw.label} installato con futurerestore",
                                     "Il dispositivo si riavvia: completa la configurazione iniziale "
                                     "(serve l'Apple ID per l'attivazione).")

        def fail(exc):
            done()
            fe = friendly_error(exc)
            self.logMessage.emit(f"⚠ [Firmware] {fe.detail}")
            self.banner.show_message(
                "error", "Downgrade non riuscito", fe.message,
                "Se il dispositivo è rimasto in modalità Recovery puoi farlo uscire qui sotto (riparte "
                "con la versione di prima, se è ancora intatta) oppure ripristinarlo con iTunes "
                "all'ultima versione firmata.",
                actions=[("Esci da Recovery", lambda: self.exit_recovery(exe))])

        start_worker(self.backend.run_futurerestore, args, on_line=lambda line: log.emit(f"  {line}"),
                     on_ok=ok, on_fail=fail)

    def exit_recovery(self, exe) -> None:
        if self._restoring:
            return
        from .workers import start_worker

        self.logMessage.emit("[Firmware] Uscita dalla modalità Recovery…")

        def ok(_):
            self.logMessage.emit("[Firmware] Dispositivo riavviato fuori dalla modalità Recovery")
            self.banner.show_message("success", "Uscita dalla modalità Recovery",
                                     "Il dispositivo si riavvia con il sistema già installato.")

        def fail(exc):
            fe = friendly_error(exc)
            self.logMessage.emit(f"⚠ [Firmware] {fe.detail}")
            self.banner.show_message("error", "Uscita dalla modalità Recovery non riuscita", fe.message,
                                     "Ripristina il dispositivo con iTunes all'ultima versione firmata.")

        start_worker(self.backend.exit_recovery, exe, on_ok=ok, on_fail=fail)


def _reveal(path) -> None:
    path = Path(path)
    if os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def _launch(exe: Path) -> None:
    try:
        os.startfile(str(exe))  # type: ignore[attr-defined]
    except Exception:
        logging.getLogger(__name__).debug("avvio di iTunes non riuscito", exc_info=True)
