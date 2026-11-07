"""Dialog di login Apple ID con verifica in due passaggi (2FA).

Due pagine nello stesso dialog: credenziali e codice a 6 cifre. Il login gira
in un LoginWorker; il dialog resta aperto per tutto il processo e mostra
stato ed errori in linea, senza finestre di messaggio aggiuntive.

Facoltativo: «Ricorda la password» salva le credenziali cifrate con Windows
DPAPI; con `auto_submit=True` il dialog parte da solo con quelle credenziali
(serve solo il codice 2FA, se Apple lo chiede).
"""
from __future__ import annotations

import logging
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..core import store
from ..core.errors import friendly_error
from . import icons, theme
from .theme import set_prop
from .widgets import button, icon_label, label
from .workers import LoginWorker

logger = logging.getLogger(__name__)


def _header(icon_name: str, title: str, subtitle: str) -> QWidget:
    t = theme.current()
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    lay.setSpacing(6)
    badge = QLabel()
    badge.setFixedSize(44, 44)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(f"background: {t.accent_soft}; border-radius: 22px;")
    badge.setPixmap(icons.pixmap(icon_name, t.accent, 22))
    lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
    ttl = label(title, "h2")
    ttl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(ttl)
    sub = label(subtitle, "muted", wrap=True)
    sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(sub)
    return w


class _InlineError(QFrame):
    def __init__(self):
        super().__init__()
        self.setProperty("banner", "error")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        lay.addWidget(icon_label("alert", theme.current().danger, 16), 0, Qt.AlignmentFlag.AlignTop)
        self.text = label("", wrap=True)
        lay.addWidget(self.text, 1)
        self.hide()

    def show_error(self, title: str, hint: str = "") -> None:
        self.text.setText(f"<b>{title}</b>" + (f"<br>{hint}" if hint else ""))
        self.show()


class LoginDialog(QDialog):
    """Raccoglie Apple ID + password e guida l'utente nella 2FA."""

    PAGE_CREDENTIALS, PAGE_CODE = 0, 1

    def __init__(self, flow, parent=None, apple_id: str = "", password: str = "",
                 remember: bool = False, auto_submit: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Accedi con Apple ID")
        self.setModal(True)
        self.setFixedWidth(440)
        self._flow = flow
        self._worker: LoginWorker | None = None
        self._busy = False
        self.session = None
        # ultimo tentativo (apple_id, password, ricorda) ed eventuale errore (titolo, suggerimento):
        # la finestra principale li usa per ripresentare il dialog già compilato
        self.last_attempt: tuple[str, str, bool] | None = None
        self.last_error: tuple[str, str] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(12)
        self._pages: list[QWidget] = []
        self._page = self.PAGE_CREDENTIALS

        # ---------------------------------------------------- credenziali
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(10)
        l1.addWidget(_header(
            "user", "Accedi con il tuo Apple ID",
            "Serve per firmare le app con il tuo account. Va bene anche un account gratuito.",
        ))
        l1.addWidget(label("Apple ID", "title"))
        self.txt_apple_id = QLineEdit(apple_id)
        self.txt_apple_id.setPlaceholderText("nome@icloud.com")
        self.txt_apple_id.setAccessibleName("Apple ID")
        l1.addWidget(self.txt_apple_id)
        l1.addWidget(label("Password", "title"))
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setAccessibleName("Password")
        self.act_reveal = QAction(icons.icon("eye", theme.current().muted), "Mostra password", self)
        self.act_reveal.setCheckable(True)
        self.act_reveal.toggled.connect(self._toggle_reveal)
        self.txt_password.addAction(self.act_reveal, QLineEdit.ActionPosition.TrailingPosition)
        self.txt_password.setText(password)
        l1.addWidget(self.txt_password)

        self.chk_remember = QCheckBox("Ricorda la password su questo computer")
        self.chk_remember.setToolTip(
            "Cifrata con Windows DPAPI: solo il tuo utente Windows su questo computer può leggerla. "
            "Con la password salvata l'accesso è automatico (serve solo il codice di verifica).")
        self.chk_remember.setChecked(remember)
        self.chk_remember.setVisible(store.credentials_supported())
        l1.addWidget(self.chk_remember)

        privacy = QHBoxLayout()
        privacy.setSpacing(6)
        privacy.addWidget(icon_label("lock", theme.current().muted, 14), 0, Qt.AlignmentFlag.AlignTop)
        privacy.addWidget(label(
            "La password viene inviata solo ad Apple. Viene salvata sul computer solo se "
            "scegli «Ricorda», cifrata in modo che solo il tuo utente Windows possa leggerla.",
            "small", wrap=True), 1)
        l1.addLayout(privacy)
        self.err_credentials = _InlineError()
        l1.addWidget(self.err_credentials)
        l1.addStretch(1)
        self._pages.append(p1)
        root.addWidget(p1)

        # ---------------------------------------------------- codice 2FA
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.setSpacing(10)
        l2.addWidget(_header(
            "shield", "Verifica in due passaggi",
            "Inserisci il codice a 6 cifre comparso sui tuoi dispositivi Apple.",
        ))
        self.txt_code = QLineEdit()
        self.txt_code.setObjectName("CodeInput")
        self.txt_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.txt_code.setPlaceholderText("••••••")
        self.txt_code.setAccessibleName("Codice di verifica")
        self.txt_code.setMaxLength(12)  # consente di incollare "123 456"
        code_font = QFont(self.txt_code.font())
        code_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 8)
        self.txt_code.setFont(code_font)
        self.txt_code.textEdited.connect(self._on_code_edited)
        l2.addWidget(self.txt_code)
        self.err_code = _InlineError()
        l2.addWidget(self.err_code)
        resend = QHBoxLayout()
        resend.addStretch(1)
        resend.addWidget(label("Non hai ricevuto il codice?", "muted"))
        self.btn_resend = button("Invia di nuovo", "link")
        self.btn_resend.clicked.connect(self._resend)
        resend.addWidget(self.btn_resend)
        resend.addStretch(1)
        l2.addLayout(resend)
        l2.addStretch(1)
        self._pages.append(p2)
        root.addWidget(p2)

        # ---------------------------------------------------- stato + azioni
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.hide()
        root.addWidget(self.busy)
        self.lbl_status = label("", "muted", wrap=True)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.hide()
        root.addWidget(self.lbl_status)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self.btn_cancel = button("Annulla")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_submit = button("Accedi", "primary")
        self.btn_submit.setDefault(True)
        self.btn_submit.clicked.connect(self._submit)
        actions.addWidget(self.btn_cancel)
        actions.addWidget(self.btn_submit)
        root.addLayout(actions)

        self.txt_apple_id.textChanged.connect(self._update_submit)
        self.txt_password.textChanged.connect(self._update_submit)
        self.txt_apple_id.returnPressed.connect(self.txt_password.setFocus)
        self.txt_password.returnPressed.connect(self._submit)
        self.txt_code.returnPressed.connect(self._submit)
        self._show_page(self.PAGE_CREDENTIALS)
        self._update_submit()
        (self.txt_password if apple_id else self.txt_apple_id).setFocus()
        if auto_submit and self.apple_id and self.password:
            # parte appena il dialog è visibile (exec avvia l'event loop)
            QTimer.singleShot(0, self._submit)

    @property
    def remember(self) -> bool:
        return self.chk_remember.isChecked()

    # ------------------------------------------------------------ stato
    @property
    def apple_id(self) -> str:
        return self.txt_apple_id.text().strip()

    @property
    def password(self) -> str:
        return self.txt_password.text()

    @property
    def code(self) -> str:
        return re.sub(r"\D", "", self.txt_code.text())

    def on_code_page(self) -> bool:
        return self._page == self.PAGE_CODE

    def _show_page(self, index: int) -> None:
        """Mostra una pagina e adatta l'altezza del dialog al contenuto visibile.

        Pagine mostrate/nascoste invece di un QStackedWidget: quello conserva
        l'altezza della pagina più grande e la 2FA resterebbe piena di vuoto.
        """
        self._page = index
        for i, page in enumerate(self._pages):
            page.setVisible(i == index)
        self._fit()

    def _fit(self) -> None:
        """Altezza giusta per la larghezza fissa (tiene conto del testo che va a capo)."""
        lay = self.layout()
        lay.activate()
        if lay.hasHeightForWidth():
            height = lay.totalHeightForWidth(self.width())
        else:
            height = self.sizeHint().height()
        self.setMinimumHeight(height)
        self.resize(self.width(), height)

    def _update_submit(self) -> None:
        busy = self._busy_flag()
        if self.on_code_page():
            ok = len(self.code) == 6
        else:
            ok = bool(self.apple_id) and bool(self.password)
        self.btn_submit.setEnabled(ok and not busy)

    def _busy_flag(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.busy.setVisible(busy)
        self.lbl_status.setVisible(bool(text))
        self.lbl_status.setText(text)
        for w in (self.txt_apple_id, self.txt_password, self.txt_code, self.btn_resend, self.chk_remember):
            w.setEnabled(not busy)
        self._update_submit()

    def _toggle_reveal(self, on: bool) -> None:
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self.act_reveal.setIcon(icons.icon("eye-off" if on else "eye", theme.current().muted))
        self.act_reveal.setText("Nascondi password" if on else "Mostra password")

    def _on_code_edited(self, text: str) -> None:
        digits = re.sub(r"\D", "", text)[:6]
        if digits != text:
            self.txt_code.setText(digits)
        self.err_code.hide()
        set_prop(self.txt_code, "invalid", "false")
        self._update_submit()
        if len(digits) == 6 and not self._busy_flag():
            self._submit()  # invio automatico appena il codice è completo

    # ------------------------------------------------------------ azioni
    def _submit(self) -> None:
        if not self.btn_submit.isEnabled():
            return
        if self.on_code_page():
            if self._worker is None:
                return
            self._set_busy(True, "Verifica del codice…")
            self._worker.submit_code(self.code)
            return

        self.err_credentials.hide()
        # credenziali memorizzate PRIMA dell'esito: se Apple risponde con un errore
        # (es. 503) restano disponibili per riprovare, e con «Ricorda» sono già su disco
        self.last_attempt = (self.apple_id, self.password, self.remember)
        self._store_credentials()
        self._set_busy(True, "Accesso in corso…")
        worker = LoginWorker(self._flow, self.apple_id, self.password)
        worker.status.connect(self._on_status)
        worker.need_2fa.connect(self._on_need_2fa)
        worker.code_rejected.connect(self._on_code_rejected)
        worker.succeeded.connect(self._on_success)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        self._worker = worker
        worker.start()

    def _resend(self) -> None:
        if self._worker is not None:
            self.err_code.hide()
            self._set_busy(True, "Invio di un nuovo codice…")
            self._worker.resend_code()

    # ---------------------------------------------------- segnali worker
    def _on_status(self, text: str) -> None:
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))
        if text.startswith("Nuovo codice inviato"):
            self._set_busy(False, text)
            self.txt_code.setFocus()

    def _on_need_2fa(self) -> None:
        self._show_page(self.PAGE_CODE)
        self.btn_submit.setText("Verifica")
        self.txt_code.clear()
        self._set_busy(False, "")
        self.txt_code.setFocus()

    def _on_code_rejected(self, message: str) -> None:
        fe = friendly_error(message)
        self._set_busy(False, "")
        self.err_code.show_error(fe.title, fe.hint)
        self._fit()
        set_prop(self.txt_code, "invalid", "true")
        self.txt_code.clear()
        self.txt_code.setFocus()

    def _on_success(self, session) -> None:
        self.session = session
        self.last_error = None
        self._set_busy(False, "")
        self._worker = None
        self.accept()

    def _store_credentials(self) -> None:
        """Salva (cifrate) o dimentica le credenziali secondo la casella «Ricorda»."""
        if not store.credentials_supported():
            return
        try:
            if self.remember:
                store.save_credentials(self.apple_id, self.password)
            else:
                store.clear_credentials()
        except Exception:
            logger.warning("Salvataggio delle credenziali non riuscito", exc_info=True)

    def _on_failed(self, exc) -> None:
        fe = friendly_error(exc)
        self._worker = None
        self._show_page(self.PAGE_CREDENTIALS)
        self.btn_submit.setText("Accedi")
        self._set_busy(False, "")
        self.last_error = (fe.title, fe.hint or fe.message)
        self.show_error(*self.last_error)
        self.txt_password.setFocus()
        self.txt_password.selectAll()

    def show_error(self, title: str, hint: str = "") -> None:
        """Errore in evidenza sotto i campi (anche quello di un tentativo precedente)."""
        self.err_credentials.show_error(title, hint)
        self._fit()

    def _on_cancelled(self) -> None:
        self._worker = None

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
        super().reject()
