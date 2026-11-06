"""Matrice di compatibilità jailbreak: tool disponibili per modello × iOS.

Ogni tool è distribuito dai suoi sviluppatori originali su GitHub: questa app
scarica solo gli IPA dalle release ufficiali e li installa con la normale
pipeline di firma. Nessun exploit è incluso o creato da noi.

Due famiglie:
  - 'sideload'  : IPA installabile con account Apple gratuito via questa app
  - 'dfu'       : exploit checkm8 (palera1n) che si esegue da PC in modalità
                  DFU: NON installabile via sideload, mostrato solo come guida

La matrice è basata sulle dichiarazioni ufficiali dei progetti. Verifica
sempre l'ultima release del repo ufficiale prima di installare.
"""
from __future__ import annotations

from dataclasses import dataclass

from .devices_catalog import DeviceModel, ios_in_range


@dataclass(frozen=True)
class JailbreakTool:
    id: str
    name: str
    description: str
    kind: str  # 'sideload' | 'dfu'
    arm64_range: tuple[str, str] | None  # iOS min-max per A7-A11
    arm64e_range: tuple[str, str] | None  # iOS min-max per A12+
    armv7_range: tuple[str, str] | None = None  # iOS min-max per A5-A6 (32-bit)
    repo: str = ""  # 'owner/repo' su GitHub (release ufficiali)
    direct_ipa_url: str = ""  # URL diretto dal sito ufficiale del progetto
    official_site: str = ""  # sito ufficiale (per installazione manuale)
    notes: str = ""


TOOLS: list[JailbreakTool] = [
    JailbreakTool(
        id="phoenix",
        name="Phoenix",
        description="Jailbreak semi-untethered per dispositivi 32-bit su iOS 9.3.4-9.3.6",
        kind="sideload",
        arm64_range=None,
        arm64e_range=None,
        armv7_range=("9.3.4", "9.3.6"),
        direct_ipa_url="https://phoenixpwn.com/download.php",
        official_site="https://phoenixpwn.com",
        notes="Dopo ogni riavvio ri-apri l'app per ri-applicarlo. Dopo il jailbreak "
              "aggiorna i certificati TLS (tlsroot.litten.ca) per far funzionare i repo moderni.",
    ),
    JailbreakTool(
        id="etasonjb",
        name="EtasonJB",
        description="Jailbreak UNTETHERED per dispositivi 32-bit su iOS 8.4.1",
        kind="sideload",
        arm64_range=None,
        arm64e_range=None,
        armv7_range=("8.4.1", "8.4.1"),
        direct_ipa_url="https://etasonjb.tihmstar.net/etasonJB-RC4.ipa",
        official_site="https://etasonjb.tihmstar.net",
        notes="L'unico untethered per 8.4.1: sopravvive ai riavvii senza ri-applicarlo.",
    ),
    JailbreakTool(
        id="h3lix",
        name="h3lix",
        description="Jailbreak per dispositivi 32-bit su iOS 10.x",
        kind="sideload",
        arm64_range=None,
        arm64e_range=None,
        armv7_range=("10.0", "10.3.4"),
        official_site="https://h3lix.link",
        notes="Il sito originale h3lix.link è offline: scarica l'IPA da una fonte "
              "verificata e usa 'Installa IPA…'.",
    ),
    JailbreakTool(
        id="dopamine",
        name="Dopamine",
        description="Jailbreak rootless (Bootstrap/ElleKit) — la scelta moderna per iOS 15-16",
        kind="sideload",
        arm64_range=("15.0", "16.6.1"),
        arm64e_range=("15.0", "15.4.1"),
        repo="opa334/Dopamine",
        notes="Dopo il jailbreak, l'app deve restare installata per ri-applicarlo al riavvio.",
    ),
    JailbreakTool(
        id="trollinstallerx",
        name="TrollInstallerX",
        description="Installa TrollStore (app 'permanenti' senza ri-firma a 7 giorni) su iOS 14-16.6.1",
        kind="sideload",
        arm64_range=("14.0", "16.6.1"),
        arm64e_range=("15.0", "16.5.1"),
        repo="alfiecg24/TrollInstallerX",
        notes="TrollStore NON è un jailbreak: installa app firmate permanenti sfruttando un bug CoreTrust. "
              "Il repo Misaka originale è stato rimosso da GitHub; TrollInstallerX è l'installer mantenuto.",
    ),
    JailbreakTool(
        id="unc0ver",
        name="unc0ver",
        description="Jailbreak classico semi-untethered — storico, per iOS 11-14",
        kind="sideload",
        arm64_range=("11.0", "14.8"),
        arm64e_range=("11.0", "14.8"),
        repo="pwn20wndstuff/Undecimus",
        notes="Progetto non più aggiornato: per iOS 11-14 resta la scelta affidabile.",
    ),
    JailbreakTool(
        id="taurine",
        name="Taurine",
        description="Jailbreak per iOS 14.0-14.3 (basato su Taurine/Protean)",
        kind="sideload",
        arm64_range=("14.0", "14.3"),
        arm64e_range=("14.0", "14.3"),
        repo="CoolStar/Taurine",
    ),
    JailbreakTool(
        id="nugget",
        name="Nugget",
        description="Personalizzazioni iOS 17-18 (SparseRestore) — NON è un jailbreak",
        kind="sideload",
        arm64_range=("17.0", "18.9"),
        arm64e_range=("17.0", "18.9"),
        repo="leminlimez/Nugget",
        notes="Modifica flag interni di sistema (feature flags): rischio basso ma non-zero.",
    ),
    JailbreakTool(
        id="palera1n",
        name="palera1n (DFU)",
        description="Jailbreak semi-tethered checkm8 per iOS 15-17 — SOLO da PC in modalità DFU",
        kind="dfu",
        arm64_range=("15.0", "17.9"),
        arm64e_range=None,  # checkm8 non copre A12+
        armv7_range=None,
        official_site="https://palera1n.github.io",
        notes="Non installabile da questa app: richiede la procedura DFU con il PC "
              "(tool ufficiale palera1n per Windows/macOS/Linux). Solo dispositivi A8-A11.",
    ),
]


@dataclass
class Compatibility:
    tool: JailbreakTool
    compatible: bool
    method: str  # 'sideload' | 'dfu'
    reason: str


def compatible_tools(model: DeviceModel, ios_version: str) -> list[Compatibility]:
    """Lista dei tool valutati per il dispositivo dato."""
    out: list[Compatibility] = []
    rng_by_arch = {"armv7": lambda t: t.armv7_range, "arm64": lambda t: t.arm64_range,
                   "arm64e": lambda t: t.arm64e_range}
    get_rng = rng_by_arch[model.arch]
    for t in TOOLS:
        rng = get_rng(t)
        if rng is None:
            if t.kind == "dfu" and not model.checkm8:
                out.append(Compatibility(t, False, t.kind, "richiede checkm8 (A8-A11)"))
            elif t.kind == "dfu":
                out.append(Compatibility(t, False, t.kind, "solo da PC in DFU"))
            else:
                out.append(Compatibility(t, False, t.kind, f"non disponibile per chip {model.chip} (32-bit)" if model.arch == "armv7" else f"non disponibile per chip {model.chip}"))
            continue
        if ios_in_range(ios_version, rng[0], rng[1]):
            if t.kind == "dfu" and not model.checkm8:
                out.append(Compatibility(t, False, t.kind, f"iOS {ios_version} ok ma serve checkm8"))
            else:
                out.append(
                    Compatibility(
                        t,
                        t.kind == "sideload",
                        t.kind,
                        f"iOS {rng[0]}–{rng[1]}",
                    )
                )
        else:
            out.append(Compatibility(t, False, t.kind, f"richiede iOS {rng[0]}–{rng[1]}"))
    return out


def find_tool(tool_id: str) -> JailbreakTool | None:
    for t in TOOLS:
        if t.id == tool_id:
            return t
    return None
