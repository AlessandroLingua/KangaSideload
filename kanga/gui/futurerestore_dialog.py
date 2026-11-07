"""Dialog per il downgrade a una versione NON firmata con futurerestore + SHSH.

Mostra cosa serve e cosa manca prima di avviare:
- futurerestore.exe (scelto dall'utente: Kanga non lo scarica da server di terzi)
- gli SHSH di quella versione per QUESTO dispositivo (ECID) e il confronto tra
  il loro nonce e quello attuale del dispositivo
- opzione pwned DFU (chip A7-A11) per impostare il nonce
- conferma esplicita del backup: il ripristino cancella tutto
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
)

from ..core import firmware as firmware_mod
from ..core.errors import friendly_error
from . import theme
from .widgets import button, icon_label, label


def _mark(ok: bool | None) -> str:
    return "✓" if ok else ("⚠" if ok is None else "✗")


class FuturerestoreDialog(QDialog):
    def __init__(self, backend, fw: firmware_mod.Firmware, info: firmware_mod.DeviceFirmwareInfo,
                 ipsw: Path, ap_nonce: bytes | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Downgrade con SHSH (futurerestore)")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.backend = backend
        self.fw, self.info, self.ipsw, self.ap_nonce = fw, info, Path(ipsw), ap_nonce
        self.exe: Path | None = backend.find_futurerestore()
        self.exe_version = backend.futurerestore_version(self.exe) if self.exe else ""
        self.blob_path: Path | None = backend.local_shsh(fw, info.ecid)
        self.blob: firmware_mod.BlobInfo | None = None
        self.check: firmware_mod.BlobCheck | None = None
        self.blob_error = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        v.addWidget(label(f"Installare {fw.label} con futurerestore", "h2"))
        v.addWidget(label(
            "Apple non firma più questa versione: futurerestore può installarla usando gli SHSH "
            "salvati quando era firmata. Funziona solo se gli SHSH sono di questo dispositivo, se il "
            "nonce coincide e se il SEP/baseband attuali sono compatibili con questa versione.",
            "muted", wrap=True))

        # --- futurerestore
        v.addWidget(label("futurerestore", "title"))
        row = QHBoxLayout()
        self.lbl_exe = label("", wrap=True)
        row.addWidget(self.lbl_exe, 1)
        b = button("Scegli futurerestore…", icon_name="folder")
        b.clicked.connect(self.choose_exe)
        row.addWidget(b)
        b_site = button("Sito del progetto", "link", icon_name="external")
        b_site.clicked.connect(lambda: webbrowser.open(firmware_mod.FUTURERESTORE_SITE))
        row.addWidget(b_site)
        v.addLayout(row)

        # --- SHSH
        v.addWidget(label("SHSH", "title"))
        row = QHBoxLayout()
        self.lbl_blob = label("", wrap=True)
        row.addWidget(self.lbl_blob, 1)
        b = button("Scegli file SHSH…", icon_name="shield")
        b.clicked.connect(self.choose_blob)
        row.addWidget(b)
        v.addLayout(row)

        # --- opzioni
        self.chk_pwndfu = QCheckBox("Il dispositivo è in pwned DFU: imposta il nonce (--use-pwndfu --set-nonce)")
        self.chk_pwndfu.setToolTip("Solo chip A7-A11, dopo averlo messo in pwned DFU con uno strumento "
                                   "come gaster. Serve quando il nonce non coincide.")
        self.chk_pwndfu.toggled.connect(self._update)
        v.addWidget(self.chk_pwndfu)
        v.addWidget(label(
            ("Modem presente: verrà installato il baseband più recente firmato (--latest-baseband)."
             if info.has_baseband else "Dispositivo senza modem: --no-baseband.")
            + " SEP: il più recente firmato (--latest-sep).", "small", wrap=True))

        # --- esito controlli
        self.issues = QFrame()
        self.issues.setProperty("banner", "warning")
        il = QHBoxLayout(self.issues)
        il.setContentsMargins(10, 8, 10, 8)
        il.addWidget(icon_label("alert", theme.current().warning, 16), 0, Qt.AlignmentFlag.AlignTop)
        self.lbl_issues = label("", wrap=True)
        il.addWidget(self.lbl_issues, 1)
        v.addWidget(self.issues)

        self.chk_backup = QCheckBox("Ho fatto un backup e so che TUTTI i dati del dispositivo verranno cancellati")
        self.chk_backup.toggled.connect(self._update)
        v.addWidget(self.chk_backup)

        actions = QHBoxLayout()
        actions.addStretch(1)
        b_cancel = button("Annulla")
        b_cancel.clicked.connect(self.reject)
        self.btn_start = button("Avvia downgrade", "primary", icon_name="refresh")
        self.btn_start.clicked.connect(self.accept)
        actions.addWidget(b_cancel)
        actions.addWidget(self.btn_start)
        v.addLayout(actions)

        self._load_blob()
        self._update()

    # ------------------------------------------------------------ dati
    @property
    def pwndfu(self) -> bool:
        return self.chk_pwndfu.isChecked()

    def args(self) -> list[str]:
        return self.backend.futurerestore_args(self.exe, self.blob_path, self.ipsw,
                                               has_baseband=self.info.has_baseband, pwndfu=self.pwndfu)

    def set_exe(self, path) -> None:
        self.exe = Path(self.backend.set_futurerestore(path))
        self.exe_version = self.backend.futurerestore_version(self.exe)
        self._update()

    def set_blob(self, path) -> None:
        self.blob_path = Path(path)
        self._load_blob()
        self._update()

    def choose_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Scegli futurerestore", str(Path.home()),
                                              "futurerestore (futurerestore*.exe futurerestore);;Tutti i file (*)")
        if path:
            try:
                self.set_exe(path)
            except Exception as e:
                self.lbl_exe.setText(friendly_error(e).message)

    def choose_blob(self) -> None:
        start = str(self.blob_path.parent if self.blob_path else self.backend.shsh_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Scegli gli SHSH", start,
                                              "SHSH (*.shsh2 *.shsh *.plist);;Tutti i file (*)")
        if path:
            self.set_blob(path)

    def _load_blob(self) -> None:
        self.blob, self.check, self.blob_error = None, None, ""
        if self.blob_path is None:
            return
        try:
            self.blob = self.backend.read_blob(self.blob_path)
            self.check = self.backend.check_blob(self.info, self.blob, self.ap_nonce)
        except Exception as e:
            self.blob_error = friendly_error(e).message

    # ------------------------------------------------------------ stato
    def problems(self) -> list[str]:
        out = []
        if self.exe is None:
            out.append("Scegli futurerestore.exe (scaricalo dal sito del progetto).")
        if self.blob_path is None:
            out.append(f"Nessun SHSH salvato per {self.fw.label} su questo dispositivo: scegli il file SHSH.")
        elif self.blob_error:
            out.append(self.blob_error)
        if self.check:
            out += self.check.problems
        return out

    def _update(self) -> None:
        if self.exe:
            self.lbl_exe.setText(f"{self.exe}" + (f"\n{self.exe_version}" if self.exe_version else ""))
        else:
            self.lbl_exe.setText("Non trovato.")
        if self.blob is not None and self.check is not None:
            nonce = self.check.nonce_ok
            self.lbl_blob.setText(
                f"{self.blob.path.name}\n"
                f"{_mark(not self.check.problems)} ECID {self.blob.ecid_hex}   "
                f"{_mark(nonce)} nonce {'coincide' if nonce else 'diverso' if nonce is False else 'non verificato'}"
                + (f"   generator {self.blob.generator}" if self.blob.generator else ""))
        elif self.blob_path is not None:
            self.lbl_blob.setText(f"{self.blob_path.name}\n✗ {self.blob_error}")
        else:
            self.lbl_blob.setText("Nessun file SHSH scelto.")
        problems = self.problems()
        warnings = self.check.warnings if self.check else []
        if self.pwndfu and self.check and self.check.nonce_ok is False:
            warnings = [w for w in warnings if "nonce" not in w]  # il nonce verrà impostato
        self.lbl_issues.setText("\n".join(f"• {t}" for t in problems + warnings))
        self.issues.setVisible(bool(problems or warnings))
        self.btn_start.setEnabled(not problems and self.chk_backup.isChecked())
