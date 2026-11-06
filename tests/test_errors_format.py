"""Messaggi d'errore comprensibili e testi dell'interfaccia."""
from datetime import datetime, timedelta, timezone

import pytest
import requests

from kanga.core.device import DeviceInfo
from kanga.core.errors import friendly_error
from kanga.core.gsa import GsaError
from kanga.core.ipa import IpaError
from kanga.core.signer import ZsignError
from kanga.gui import format as fmt


@pytest.mark.parametrize("err, title", [
    (GsaError("Apple sta respingendo ... (HTTP 503 all'endpoint GsService2 — -80045)"), "Apple ha rifiutato l'accesso"),
    (GsaError("Init SRP fallito: errore -80046"), "Apple ha rifiutato l'accesso"),
    (GsaError("Login fallito: Your Apple ID or password was incorrect", apple_code=-22406), "Apple ID o password errati"),
    ("Codice 2FA rifiutato (HTTP 401). Riprova con un nuovo codice.", "Codice di verifica non valido"),
    (ZsignError("zsign non trovato: scaricalo"), "Componente di firma mancante"),
    (ZsignError("zsign fallito (exit 1): bad"), "Firma dell'app non riuscita"),
    (IpaError("Il file non contiene un'app iOS"), "File IPA non valido"),
    ("IPA non trovata: C:/x.ipa", "File IPA non trovato"),
    ("Servizio Apple Mobile Device assente", "Servizio Apple Mobile Device assente"),
    ("Installazione fallita: ApplicationVerificationFailed: 0xe8008021 (The maximum number of apps for "
     "free development profiles has been reached.)", "Limite di 3 app raggiunto"),
    ("Installazione fallita: ApplicationVerificationFailed: signature", "iOS ha rifiutato l'app"),
    ("Installazione fallita: DeviceOSVersionTooLow", "Versione di iOS troppo vecchia"),
    ("Pairing fallito con X: NotPairedError", "iPhone non autorizzato"),
    ("Apple resultCode 9401: You can only create 10 App IDs every 7 days", "Limite di App ID raggiunto"),
    ("Nessun team di sviluppo associato all'account", "Account sviluppatore non attivo"),
    (requests.ConnectionError("Max retries exceeded"), "Connessione non riuscita"),
    (RuntimeError("boh"), "Operazione non riuscita"),
])
def test_friendly_error_titles(err, title):
    fe = friendly_error(err)
    assert fe.title == title
    assert fe.detail  # il testo tecnico resta disponibile per il registro


def test_friendly_error_uses_exception_cause():
    class PasswordRequiredError(Exception):
        pass

    try:
        try:
            raise PasswordRequiredError("locked")
        except PasswordRequiredError as inner:
            raise RuntimeError("Pairing non riuscito") from inner
    except RuntimeError as e:
        assert friendly_error(e).title == "iPhone bloccato"


def test_friendly_error_fallback_keeps_message():
    fe = friendly_error(RuntimeError("qualcosa di strano"))
    assert fe.message == "qualcosa di strano"
    assert "Registro" in fe.hint


def test_expiry_status():
    now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    assert fmt.expiry_status(now + timedelta(days=6, hours=2), now) == ("Scade tra 6 giorni", "ok")
    assert fmt.expiry_status(now + timedelta(days=2, hours=1), now) == ("Scade tra 2 giorni", "warn")
    assert fmt.expiry_status(now + timedelta(days=1, hours=1), now) == ("Scade domani", "warn")
    assert fmt.expiry_status(now + timedelta(hours=5), now) == ("Scade tra 5 ore", "error")
    assert fmt.expiry_status(now + timedelta(minutes=10), now) == ("Scade tra 1 ora", "error")
    assert fmt.expiry_status(now - timedelta(seconds=1), now) == ("Scaduta", "error")
    assert fmt.expiry_status("", now) == ("Scadenza sconosciuta", "neutral")
    assert fmt.expiry_status((now + timedelta(days=3)).isoformat(), now)[0] == "Scade tra 3 giorni"
    # date senza fuso orario: interpretate come UTC
    assert fmt.expiry_status("2026-11-10T12:00:00", now) == ("Scade tra 4 giorni", "ok")


def test_device_texts():
    ok = DeviceInfo("U1", "iPhone di Mario", "18.1", "iPhone15,2", True, "ok")
    assert fmt.device_label(ok) == "iPhone di Mario · iOS 18.1"
    assert fmt.device_status(ok) == ("Pronto", "ok")
    pending = DeviceInfo("U2", "", trusted=False, status="pending", connection="Network")
    assert fmt.device_label(pending) == "Dispositivo iOS · Wi-Fi · da autorizzare"
    assert fmt.device_status(pending)[1] == "warn"
    assert "Autorizza" in fmt.device_help(pending)
    unknown = DeviceInfo("U3", "X", status="boh")
    assert fmt.device_status(unknown) == ("Non raggiungibile", "error")


def test_join_and_dates():
    assert fmt.join_requirements([]) == ""
    assert fmt.join_requirements(["a"]) == "a"
    assert fmt.join_requirements(["a", "b"]) == "a e b"
    assert fmt.join_requirements(["a", "b", "c"]) == "a, b e c"
    assert fmt.format_date("2026-11-06T10:00:00+00:00").endswith("/11/2026")
    assert fmt.format_date("not a date") == ""
