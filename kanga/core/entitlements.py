"""Generazione e gestione entitlements per la firma con account Apple gratuito.

Per gli account gratuiti Apple concede solo entitlements base (nessun App
Group, iCloud, Associated Domains, ecc.): i profili free non li contengono e
la firma verrebbe rifiutata all'installazione se non coerenti col profilo.
"""
from __future__ import annotations

import plistlib

# Entitlements che gli account gratuiti NON possono usare: vengono rimossi
# dagli entitlements originali dell'app prima di generare il profilo/free firma.
FREE_ACCOUNT_FORBIDDEN_PREFIXES = (
    "com.apple.developer.icloud",
    "com.apple.developer.ubiquity",
    "com.apple.security.application-groups",
    "com.apple.developer.associated-domains",
    "com.apple.developer.pass-type-identifiers",
    "com.apple.developer.usernotifications",
    "com.apple.developer.default-data-protection",
    "com.apple.developer.healthkit",
    "com.apple.developer.homekit",
    "com.apple.developer.wallet",
    "com.apple.developer.siri",
    "com.apple.developer.networking.",
    "com.apple.external-accessory.wishlist",
    "com.apple.developer.game-center",
    "com.apple.developer.in-app-payments",
    "com.apple.developer.applesignin",
    "com.apple.developer.devicecheck",
    "com.apple.developer ClassKit",  # defensive
)

# Entitlements "liberi" dell'app che vengono preservati se presenti
PRESERVED_ENTITLEMENTS = {
    "com.apple.developer.kernel.extended-virtual-addressing",
    "com.apple.developer.kernel.increased-memory-limit",
    "com.apple.developer.networking.wifi-info",
    "com.apple.security.network.client",
    "com.apple.security.network.server",
}


def free_provisioning_entitlements(team_id: str, bundle_id: str) -> bytes:
    """Entitlements minimi validi per un profilo free (come AltStore/Sideloadly)."""
    ents = {
        "application-identifier": f"{team_id}.{bundle_id}",
        "com.apple.developer.team-identifier": team_id,
        "get-task-allow": True,
        "keychain-access-groups": [f"{team_id}.{bundle_id}"],
    }
    return plistlib.dumps(ents, fmt=plistlib.FMT_XML)


def strip_entitlements_for_free_account(entitlements: dict) -> dict:
    """Rimuove gli entitlements vietati per account gratuito, preservando quelli liberi."""
    out = {}
    for key, value in entitlements.items():
        if key in PRESERVED_ENTITLEMENTS:
            out[key] = value
        elif not any(key.startswith(p.strip()) or key == p for p in FREE_ACCOUNT_FORBIDDEN_PREFIXES):
            out[key] = value
    return out


def parse_app_entitlements(ipa_path, progress=None) -> dict | None:
    """Estrae entitlements grezzi dall'app principale dell'IPA (embedded CMS blob).

    Parsing minimale: cerca il magic `0xfade0c00` (entitlements blob) nel
    binario principale e decodifica. Se fallisce, restituisce None (non è
    bloccante: si useranno i soli entitlements base).
    """
    import io
    import struct
    import zipfile
    from pathlib import Path

    ipa_path = Path(ipa_path)
    try:
        with zipfile.ZipFile(ipa_path) as z:
            names = z.namelist()
            # binario principale = CFBundleExecutable dentro Info.plist
            info_plist = None
            for n in names:
                if n.endswith("Payload/") is False and n.count("/") == 2 and n.endswith("Info.plist"):
                    info_plist = n
                    break
            if info_plist is None:
                return None
            app_dir = info_plist.rsplit("/", 1)[0]
            with z.open(info_plist) as f:
                pl = plistlib.load(f)
            exe_name = pl.get("CFBundleExecutable")
            if not exe_name:
                return None
            exe_path = f"{app_dir}/{exe_name}"
            with z.open(exe_path) as f:
                data = f.read()
    except Exception:
        return None

    # cerca entitlements blob: magic 0xfade0c00 (length, type=0x00, ...)
    magic = b"\xfa\xde\x0c\x00"
    idx = data.find(magic)
    while idx != -1:
        try:
            (length, blob_type) = struct.unpack_from(">II", data, idx)
            if blob_type == 0 and 8 < length < 1_000_000 and idx + length <= len(data):
                blob = data[idx + 8 : idx + length]
                try:
                    return plistlib.loads(blob)
                except Exception:
                    pass
        except struct.error:
            break
        idx = data.find(magic, idx + 4)
    return None
