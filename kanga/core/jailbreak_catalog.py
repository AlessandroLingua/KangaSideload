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
    repo: str  # 'owner/repo' su GitHub
    description: str
    kind: str  # 'sideload' | 'dfu'
    arm64_range: tuple[str, str] | None  # iOS min-max per A8-A11
    arm64e_range: tuple[str, str] | None  # iOS min-max per A12+
    min_ram_gb: int = 0  # alcuni tool (es. palera1n rootless) consigliano ram
    notes: str = ""


TOOLS: list[JailbreakTool] = [
    JailbreakTool(
        id="dopamine",
        name="Dopamine",
        repo="opa334/Dopamine",
        description="Jailbreak rootless (Bootstrap/ElleKit) — la scelta moderna per iOS 15-16",
        kind="sideload",
        arm64_range=("15.0", "16.6.1"),
        arm64e_range=("15.0", "15.4.1"),
        notes="Dopo il jailbreak, l'app deve restare installata per ri-applicarlo al riavvio.",
    ),
    JailbreakTool(
        id="trollinstallerx",
        name="TrollInstallerX",
        repo="alfiecg24/TrollInstallerX",
        description="Installa TrollStore (app 'permanenti' senza ri-firma a 7 giorni) su iOS 14-16.6.1",
        kind="sideload",
        arm64_range=("14.0", "16.6.1"),
        arm64e_range=("15.0", "16.5.1"),
        notes="TrollStore NON è un jailbreak: installa app firmate permanenti sfruttando un bug CoreTrust. "
              "Il repo Misaka originale è stato rimosso da GitHub; TrollInstallerX è l'installer mantenuto.",
    ),
    JailbreakTool(
        id="unc0ver",
        name="unc0ver",
        repo="pwn20wndstuff/Undecimus",
        description="Jailbreak classico semi-untethered — storico, per iOS 11-14",
        kind="sideload",
        arm64_range=("11.0", "14.8"),
        arm64e_range=("11.0", "14.8"),
        notes="Progetto non più aggiornato: per iOS 11-14 resta la scelta affidabile.",
    ),
    JailbreakTool(
        id="taurine",
        name="Taurine",
        repo="CoolStar/Taurine",
        description="Jailbreak per iOS 14.0-14.3 (basato su Taurine/Protean)",
        kind="sideload",
        arm64_range=("14.0", "14.3"),
        arm64e_range=("14.0", "14.3"),
    ),
    JailbreakTool(
        id="nugget",
        name="Nugget",
        repo="leminlimez/Nugget",
        description="Personalizzazioni iOS 17-18 (SparseRestore) — NON è un jailbreak",
        kind="sideload",
        arm64_range=("17.0", "18.9"),
        arm64e_range=("17.0", "18.9"),
        notes="Modifica flag interni di sistema (feature flags): rischio basso ma non-zero.",
    ),
    JailbreakTool(
        id="palera1n",
        name="palera1n (DFU)",
        repo="palera1n/palera1n",
        description="Jailbreak semi-tethered checkm8 per iOS 15-17 — SOLO da PC in modalità DFU",
        kind="dfu",
        arm64_range=("15.0", "17.9"),
        arm64e_range=None,  # checkm8 non copre A12+
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
    for t in TOOLS:
        rng = t.arm64_range if model.arch == "arm64" else t.arm64e_range
        if rng is None:
            if t.kind == "dfu" and not model.checkm8:
                out.append(Compatibility(t, False, t.kind, "richiede checkm8 (A8-A11)"))
            elif t.kind == "dfu":
                out.append(Compatibility(t, False, t.kind, "solo da PC in DFU"))
            else:
                out.append(Compatibility(t, False, t.kind, f"non disponibile per chip {model.chip}"))
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
