# Kanga Sideload

Sideloader iOS **open source** per Windows, scritto da zero con componenti
open source. Tutte le funzionalità attive da subito, nessun paywall.

Include un **tab Jailbreak**: rileva il dispositivo collegato (iPhone, iPad,
iPod touch), mostra i tool di jailbreak compatibili per il tuo modello × iOS
e li installa direttamente scaricandoli **solo dalle release GitHub ufficiali**
dei rispettivi progetti:

| Tool | iOS (arm64 A8-A11) | iOS (arm64e A12+) | Tipo |
|---|---|---|---|
| Dopamine | 15.0 – 16.6.1 | 15.0 – 15.4.1 | Jailbreak (sideload) |
| TrollInstallerX | 14.0 – 16.6.1 | 15.0 – 16.5.1 | Installer TrollStore |
| unc0ver | 11.0 – 14.8 | 11.0 – 14.8 | Jailbreak (sideload) |
| Taurine | 14.0 – 14.3 | 14.0 – 14.3 | Jailbreak (sideload) |
| Nugget | 17.0 – 18.x | 17.0 – 18.x | Personalizzazione (non JB) |
| palera1n | 15.0 – 17.x | ✗ (solo A8-A11) | **DFU da PC** (guida, non sideload) |

⚠ Il repo Misaka originale è stato rimosso da GitHub: per installare
TrollStore si usa TrollInstallerX (alfiecg24), l'installer mantenuto.
I jailbreak checkm8 (palera1n) non sono installabili via sideload: richiedono
la procedura DFU dal PC — il tab mostra la compatibilità e la guida.

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
uv pip install --python .venv/Scripts/python.exe -e .
# binario di firma (una tantum)
.venv/Scripts/python.exe scripts/fetch_zsign.py
```

## Uso

```bash
.venv/Scripts/python.exe -m kanga
```

1. Collega l'iPhone via USB e premi **Aggiorna** (accetta "Autorizza" sul telefono
   se richiesto)
2. **Accedi con Apple ID** (il codice 2FA arriva sui tuoi dispositivi Apple)
3. Trascina un `.ipa` e clicca **Firma e installa**

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

- La password dell'Apple ID può essere salvata **opzionalmente** (checkbox nel
  dialog di login): viene cifrata con **Windows DPAPI** e solo il tuo utente
  Windows su questa macchina può decifrarla. Mai in chiaro su disco. Per
  dimenticarla: deseleziona la checkbox e rifai il login, oppure cancella
  `%LOCALAPPDATA%/Kanga/credentials.kanga.json`
- Il token di sessione resta in `%LOCALAPPDATA%/Kanga/` (cartella del tuo
  profilo utente); cancellala per fare logout completo
- Nessun dato inviato a server terzi (l'anisette è locale)
- Con credenziali salvate: all'apertura dell'app (o quando riprovi un sideload
  con sessione scaduta) il login è automatico — ti chiede solo il codice 2FA

## Struttura del codice

```
kanga/
├── __main__.py            avvio GUI
├── core/
│   ├── appflow.py         orchestrazione end-to-end
│   ├── gsa.py             login GrandSlam (SRP, spd, 2FA, Xcode token)
│   ├── anisette_provider.py  header anisette persistente
│   ├── apple_services.py  team/device/cert/CSR/AppID/profilo + P12
│   ├── signer.py          wrapper zsign
│   ├── entitlements.py    entitlements per account free + strip
│   ├── device.py          pymobiledevice3 (lista, pair, install)
│   ├── store.py           persistenza sessione in %LOCALAPPDATA%
│   └── _vendor/srp/       SRP-6a (cocagne/pysrp, MIT)
├── gui/                   PySide6 (finestra, login, worker)
└── tools/zsign/           binario scaricato da scripts/fetch_zsign.py
```

## Test

```bash
.venv/Scripts/python.exe tests/test_signing.py
```

Verifica: parsing IPA, firma ad-hoc zsign (CodeResources + signature blob),
entitlements free, pipeline CSR→P12.

## Ringazi

- cocagne/pysrp, malmeloo/Anisette.py, doronz88/pymobiledevice3,
  zhlynn/zsign, Dadoum/Sideloader, EmLoadServer, SideStore, AltStore —
  il lavoro open source su cui questa architettura si fonda.
