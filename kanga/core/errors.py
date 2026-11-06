"""Traduzione degli errori tecnici in messaggi comprensibili per l'utente.

Ogni errore diventa un `FriendlyError` con titolo, spiegazione e un
suggerimento concreto su cosa fare. Il testo tecnico originale resta in
`detail` (finisce nel registro, non nella finestra di dialogo).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FriendlyError:
    title: str
    message: str
    hint: str = ""
    detail: str = ""


def _text(err) -> str:
    if isinstance(err, BaseException):
        return str(err) or err.__class__.__name__
    return str(err or "")


def _class_names(err) -> set[str]:
    if not isinstance(err, BaseException):
        return set()
    names = {cls.__name__ for cls in type(err).__mro__}
    cause = err.__cause__ or err.__context__
    if isinstance(cause, BaseException):
        names |= {cls.__name__ for cls in type(cause).__mro__}
    return names


def _has(text: str, *needles: str) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def friendly_error(err) -> FriendlyError:
    """Converte un'eccezione (o un messaggio) in un FriendlyError."""
    text = _text(err)
    classes = _class_names(err)

    def make(title: str, message: str, hint: str = "") -> FriendlyError:
        return FriendlyError(title=title, message=message, hint=hint, detail=text)

    # --- login Apple -------------------------------------------------------
    if _has(text, "-80045", "-80046") or ("GsaError" in classes and "503" in text) or _has(
        text, "HTTP 503"
    ):
        return make(
            "Apple ha rifiutato l'accesso",
            "Apple sta bloccando gli accessi provenienti da strumenti non ufficiali "
            "(problema noto che colpisce anche altri sideloader).",
            "Riprova più tardi. Se avevi già una sessione salvata non serve rifare l'accesso.",
        )
    if _has(text, "-22406", "credenziali non valide", "incorrect", "password errat"):
        return make(
            "Apple ID o password errati",
            "Apple non ha riconosciuto le credenziali inserite.",
            "Controlla l'indirizzo e la password del tuo Apple ID (non usare una "
            "password specifica per le app).",
        )
    if _has(text, "Codice 2FA rifiutato"):
        return make(
            "Codice di verifica non valido",
            "Il codice inserito è errato o scaduto.",
            "Usa il codice più recente mostrato sui tuoi dispositivi Apple, "
            "oppure richiedine uno nuovo.",
        )

    # --- strumenti locali --------------------------------------------------
    if _has(text, "zsign") and _has(text, "non trovato", "not found", "mancante"):
        return make(
            "Componente di firma mancante",
            "Il programma di firma zsign non è installato.",
            "Usa «Scarica zsign» nella barra in basso, oppure esegui "
            "scripts/fetch_zsign.py.",
        )
    if _has(text, "zsign fallito", "zsign ad-hoc fallito") or "ZsignError" in classes:
        return make(
            "Firma dell'app non riuscita",
            "zsign non è riuscito a firmare il file.",
            "L'IPA potrebbe essere danneggiato o cifrato: le app prese dall'App Store "
            "sono cifrate e vanno prima decriptate. Dettagli nel Registro.",
        )
    if "IpaError" in classes:
        return make("File IPA non valido", text, "Scegli un altro file .ipa.")
    if _has(text, "IPA non trovata", "File non trovato"):
        return make(
            "File IPA non trovato",
            "Il file selezionato non esiste più.",
            "Potrebbe essere stato spostato o eliminato: selezionalo di nuovo.",
        )

    # --- dispositivo -------------------------------------------------------
    if _has(text, "Apple Mobile Device") or "ConnectionFailedToUsbmuxdError" in classes:
        return make(
            "Servizio Apple Mobile Device assente",
            "Kanga non riesce a comunicare con i dispositivi iOS collegati.",
            "Installa iTunes (versione classica) oppure l'app «Apple Devices» dal "
            "Microsoft Store, poi riapri Kanga.",
        )
    if _has(text, "maximum number of apps", "MaximumAppsReached", "0xe8008021"):
        return make(
            "Limite di 3 app raggiunto",
            "Con un Apple ID gratuito puoi avere al massimo 3 app installate "
            "contemporaneamente su ogni dispositivo.",
            "Elimina dal telefono un'app installata con Kanga (o con un altro "
            "sideloader) e riprova.",
        )
    if _has(text, "DeviceOSVersionTooLow"):
        return make(
            "Versione di iOS troppo vecchia",
            "Questa app richiede una versione di iOS più recente di quella del dispositivo.",
            "Aggiorna iOS oppure usa una versione meno recente dell'app.",
        )
    if _has(text, "ApplicationVerificationFailed"):
        return make(
            "iOS ha rifiutato l'app",
            "Il dispositivo non ha accettato la firma dell'app.",
            "Riprova: Kanga rigenera il profilo a ogni installazione. Se l'app "
            "contiene estensioni, potrebbero non essere compatibili con un account gratuito.",
        )
    if classes & {"PasswordRequiredError"} or _has(text, "PasswordProtected"):
        return make(
            "iPhone bloccato",
            "Il dispositivo è bloccato con il codice.",
            "Sblocca l'iPhone e riprova.",
        )
    if classes & {"NotPairedError", "PairingError", "PairingDialogResponsePendingError",
                  "UserDeniedPairingError"} or _has(text, "Pairing fallito", "NotPaired"):
        return make(
            "iPhone non autorizzato",
            "Questo computer non è ancora autorizzato dal dispositivo.",
            "Sblocca l'iPhone, tocca «Autorizza» e inserisci il codice, poi premi «Abbina».",
        )

    # --- Apple Developer ---------------------------------------------------
    if _has(text, "App ID") and _has(text, "maximum", "limit", "7 days", "10 "):
        return make(
            "Limite di App ID raggiunto",
            "Un Apple ID gratuito può creare al massimo 10 App ID ogni 7 giorni.",
            "Reinstalla app già firmate in precedenza (riusano l'App ID) oppure "
            "attendi qualche giorno.",
        )
    if _has(text, "Nessun team"):
        return make(
            "Account sviluppatore non attivo",
            "Questo Apple ID non ha ancora un team di sviluppo.",
            "Accedi una volta a developer.apple.com con questo Apple ID e accetta "
            "i termini, poi riprova.",
        )

    # --- rete --------------------------------------------------------------
    if classes & {"ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout", "SSLError"} or _has(
        text, "Max retries exceeded", "timed out", "NameResolutionError", "Failed to resolve"
    ):
        return make(
            "Connessione non riuscita",
            "Impossibile contattare i server Apple.",
            "Controlla la connessione a Internet (e proxy/firewall), poi riprova.",
        )

    return make(
        "Operazione non riuscita",
        text or "Si è verificato un errore imprevisto.",
        "Apri il Registro per i dettagli tecnici.",
    )
