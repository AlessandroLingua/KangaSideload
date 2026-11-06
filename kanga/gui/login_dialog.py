"""Dialog di login Apple ID con gestione 2FA."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class LoginDialog(QDialog):
    """Raccoglie Apple ID + password; al segnale 2FA mostra il campo codice."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Accedi con Apple ID")
        self.setMinimumWidth(420)
        self._code: str | None = None

        layout = QVBoxLayout(self)
        info = QLabel(
            "Le credenziali vengono usate solo per la richiesta di firma ad Apple "
            "(protocollo GrandSlam) e non vengono salvate. Il codice 2FA arriva "
            "sui tuoi dispositivi Apple fidati."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.txt_apple_id = QLineEdit()
        self.txt_apple_id.setPlaceholderText("nome@example.com")
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        form.addRow("Apple ID:", self.txt_apple_id)
        form.addRow("Password:", self.txt_password)
        layout.addLayout(form)

        self.frame_2fa = QFrame()
        form2 = QFormLayout(self.frame_2fa)
        self.lbl_2fa = QLabel("Codice 2FA inviato: inseriscilo qui")
        self.txt_2fa = QLineEdit()
        self.txt_2fa.setPlaceholderText("123456")
        form2.addRow(self.lbl_2fa)
        form2.addRow("Codice:", self.txt_2fa)
        self.frame_2fa.setVisible(False)
        layout.addWidget(self.frame_2fa)

        self.btn_login = QPushButton("Accedi")
        self.btn_login.setDefault(True)
        self.btn_login.clicked.connect(self._on_submit)
        layout.addWidget(self.btn_login)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

    def _on_submit(self):
        if self.frame_2fa.isVisible():
            code = self.txt_2fa.text().strip()
            if not code.isdigit() or len(code) != 6:
                QMessageBox.warning(self, "Codice non valido", "Il codice 2FA è di 6 cifre.")
                return
            self._code = code
            self.accept()
            return
        if not self.txt_apple_id.text().strip() or not self.txt_password.text():
            QMessageBox.warning(self, "Dati mancanti", "Inserisci Apple ID e password.")
            return
        self.accept()

    # ------------------------------------------------------------ API
    @property
    def apple_id(self) -> str:
        return self.txt_apple_id.text().strip()

    @property
    def password(self) -> str:
        return self.txt_password.text()

    def show_2fa(self):
        self.frame_2fa.setVisible(True)
        self.btn_login.setText("Conferma codice 2FA")
        self.txt_2fa.setFocus()

    def status(self, text: str):
        self.lbl_status.setText(text)

    def code(self) -> str | None:
        return self._code

    def reset_for_retry(self):
        self._code = None
