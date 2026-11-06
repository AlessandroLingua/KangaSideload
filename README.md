# Kanga Sideload

Sideloader iOS **open source** per Windows, scritto da zero con componenti
open source. Tutte le funzionalità attive da subito, nessun paywall.

![Kanga Sideload: installazione completata](docs/screenshot-installa.png)

## Com'è fatto

| Componente | Tecnologia | Licenza |
|---|---|---|
| GUI desktop | PySide6 (Qt 6) | LGPL |
| Comunicazione iPhone | pymobiledevice3 (usbmux, lockdown, installation proxy) | GPL-3.0 |
| Anisette locale | pacchetto `anisette` (macchina virtuale Apple emulata con Unicorn) | MIT |
| Login Apple ID | `kanga/core/gsa.py` (GrandSlam SRP-6a variante Apple + 2FA) | nostro |
| Certificato/profilo | `kanga/core/apple_services.py` (developerservices2 QH65B2) | nostro |
| Firma IPA | zsign (binari ufficiali per Windows) | MIT |
| SRP-6a | vendorizzato da cocagne/pysrp in `kanga/core/_vendor/` | MIT |

Licenza del progetto: **GPL-3.0-or-later** (compatibile con pymobiledevice3).

## Requisiti

- Windows 10/11
- Python 3.12 (gestito via [uv](https://docs.astral.sh/uv/))
- **iTunes classico oppure app "Apple Devices"** dal Microsoft Store (forniscono
  il servizio `Apple Mobile Device` per comunicare con l'iPhone via USB)
- Un Apple ID (gratuito va bene) con 2FA attiva — come per Sideloadly/AltStore

## Installazione

```bash
cd KangaSideload
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
```

Il binario di firma **zsign** si scarica dall'app stessa (pulsante «Scarica
zsign» nella barra in basso) oppure una tantum da riga di comando:

```bash
.venv/Scripts/python.exe scripts/fetch_zsign.py
```

## Uso

```bash
.venv/Scripts/python.exe -m kanga
```

La finestra **Installa** guida in tre passi e dice sempre cosa manca per
procedere:

1. **Account Apple** — «Accedi» apre il login; il codice di verifica (2FA) si
   inserisce nello stesso dialog, con invio automatico alla sesta cifra e
   «Invia di nuovo» se il codice non arriva. Un codice sbagliato non annulla il
   login: basta inserirne un altro
2. **Dispositivo** — l'iPhone viene rilevato da solo appena lo colleghi via USB
   (nessun bisogno di premere Aggiorna). Se il computer non è ancora autorizzato
   compare «Abbina»: sblocca il telefono e tocca «Autorizza»
3. **App** — trascina un `.ipa` nella finestra o sceglilo dal computer: vedi
   icona, nome, versione, iOS minimo e bundle id. Facoltativo: un nome diverso
   per la schermata Home

Poi **Firma e installa**: l'avanzamento mostra ogni passo (account,
dispositivo, certificato, App ID, profilo, firma, installazione). Alla fine
Kanga spiega cosa fare sul telefono (autorizzare lo sviluppatore e, da iOS 16,
attivare la Modalità sviluppatore); in caso di errore mostra cosa è successo e
come risolverlo, con «Riprova» e i dettagli tecnici nel **Registro**.

La sezione **Recenti** elenca le app installate con la data di scadenza della
firma (7 giorni con un account gratuito) e permette di **reinstallarle con un
clic** per rinnovarla; all'avvio Kanga avvisa se una firma sta per scadere.

Scorciatoie: `Ctrl+O` scegli IPA · `F5` cerca dispositivi · `Ctrl+Invio`
firma e installa · `Ctrl+1/2/3` cambia sezione.

Il tema chiaro/scuro segue quello di Windows (forzabile con
`KANGA_THEME=light|dark`).

<p>
  <img src="docs/screenshot-scuro.png" alt="Tema scuro" width="49%">
  <img src="docs/screenshot-2fa.png" alt="Verifica in due passaggi" width="49%">
</p>

Al primo avvio dall'app installata: *Impostazioni → Generali → VPN e gestione
dispositivo → autorizza il profilo sviluppatore*.

## Limiti Apple (insuperabili, valgono per qualunque sideloader)

- Account gratuito: firma valida **7 giorni**, max **3 app per dispositivo**,
  10 App ID/settimana
- Un account Developer a pagamento ($99/anno) rimuove i limiti (percorso da
  aggiungere in `apple_services.py`)

## Stato noto (novembre 2026): blocco Apple sul login GSA

Da ottobre 2025 Apple sta respingendo i login GrandSlam che usano anisette
"non-Mac" (macchine virtuali ADI emulata) con errori -80045/-80046 e, nelle
onde più recenti, 503 all'endpoint di login. **Anche Sideloadly è stato
colpito** (giorni: fine ottobre 2026); gli strumenti che funzionano usano
l'anisette via librerie iTunes/kbsync ("macchina reale").

Conseguenze pratiche:
- il provisioning anisette locale di questo tool **funziona** (verificato),
  ma il POST di login può ricevere 503: l'app lo segnala chiaramente
- la sessione Apple, una volta ottenuta, viene salvata in
  `%LOCALAPPDATA%/Kanga/session.kanga.json` e dura finché Apple non la
  invalida — riusata ad ogni avvio senza ri-login
- **estensione futura**: modalità anisette via iTunes/kbsync (le DLL Apple
  installate da iTunes), che è la strada seguita da Sideloadly per evitare
  il blocco

## Privacy

- La **password** dell'Apple ID non viene mai salvata su disco
- Il token di sessione resta in `%LOCALAPPDATA%/Kanga/` (cartella del tuo
  profilo utente) insieme all'indirizzo Apple ID, usato solo per mostrarlo
  nell'interfaccia; «Esci» lo elimina, cancella la cartella per un reset completo
- Lo storico delle installazioni (`history.json`, per la sezione Recenti) e la
  chiave del certificato di sviluppo stanno nella stessa cartella
- Nessun dato inviato a server terzi (l'anisette è locale)

## Struttura del codice

```
kanga/
├── __main__.py            avvio GUI
├── core/                  logica, senza dipendenze dalla GUI
│   ├── appflow.py         orchestrazione end-to-end (passi + eventi)
│   ├── gsa.py             login GrandSlam (SRP, spd, 2FA, Xcode token)
│   ├── anisette_provider.py  header anisette persistente
│   ├── apple_services.py  team/device/cert/CSR/AppID/profilo + P12
│   ├── ipa.py             metadati IPA + icona (conversione PNG CgBI)
│   ├── errors.py          errori tecnici -> messaggi comprensibili
│   ├── signer.py          wrapper zsign (output in tempo reale)
│   ├── zsign_fetch.py     download di zsign
│   ├── entitlements.py    entitlements per account free + strip
│   ├── device.py          pymobiledevice3 (lista, pair, install)
│   ├── store.py           sessione + storico in %LOCALAPPDATA%
│   └── _vendor/srp/       SRP-6a (cocagne/pysrp, MIT)
├── gui/                   PySide6
│   ├── main_window.py     finestra (Installa · Recenti · Registro)
│   ├── login_dialog.py    login + 2FA
│   ├── workers.py         thread di lavoro (risultati sempre sul thread UI)
│   ├── widgets.py         card, drop zone, step, banner
│   ├── theme.py · icons.py · format.py
└── tools/zsign/           binario scaricato da scripts/fetch_zsign.py
```

## Test

```bash
.venv/Scripts/python.exe -m pytest
```

La suite (circa 90 test, pochi secondi) gira senza iPhone né server Apple:
servizi Apple, dispositivo e zsign sono simulati. Copre parsing IPA e icone
CgBI, certificati/chiavi/P12/profili, il flusso completo di sideload, il modulo
dispositivo (API sync e async di pymobiledevice3), i worker Qt e
l'interfaccia (login con 2FA, rilevamento dispositivi, installazione riuscita
o fallita, Recenti). La GUI usa Qt *offscreen*, quindi i test funzionano anche
in CI. Il test di firma con lo zsign reale viene saltato se zsign non è
installato.

## Novità 0.2.0

- Interfaccia ridisegnata: tre passi guidati, drop zone con icona e dettagli
  dell'app, avanzamento per passi, messaggi di errore con soluzione, tema
  chiaro/scuro, sezione Recenti con scadenze e reinstallazione
- Dispositivi rilevati automaticamente; abbinamento dall'app
- Correzioni: la 2FA restava bloccata in attesa del codice; il registro veniva
  aggiornato da thread non-GUI (possibili crash); con pymobiledevice3 ≥ 11.50
  l'installazione non avveniva (coroutine mai attesa); `ProvisioningProfile`
  andava in crash leggendo il team; il certificato riusato poteva non
  corrispondere alla chiave salvata; il certificato restituito da Apple in DER
  non veniva letto; il .p12 con la chiave privata restava in %TEMP% se la firma
  falliva; «Abbina» falliva sempre

## Ringraziamenti

- cocagne/pysrp, malmeloo/Anisette.py, doronz88/pymobiledevice3,
  zhlynn/zsign, Dadoum/Sideloader, EmLoadServer, SideStore, AltStore —
  il lavoro open source su cui questa architettura si fonda.
