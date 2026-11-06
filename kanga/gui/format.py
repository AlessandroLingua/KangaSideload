"""Testi dell'interfaccia derivati dai dati (funzioni pure, testabili senza Qt)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..core import device as device_mod
from ..core.device import DeviceInfo

# (testo, tipo pill) per ogni stato del dispositivo
_DEVICE_STATUS = {
    device_mod.STATUS_OK: ("Pronto", "ok"),
    device_mod.STATUS_PENDING: ("Tocca «Autorizza» sull'iPhone", "warn"),
    device_mod.STATUS_LOCKED: ("Sblocca l'iPhone", "warn"),
    device_mod.STATUS_DENIED: ("Autorizzazione rifiutata", "error"),
    device_mod.STATUS_UNPAIRED: ("Da autorizzare", "warn"),
    device_mod.STATUS_ERROR: ("Non raggiungibile", "error"),
}

_DEVICE_HELP = {
    device_mod.STATUS_OK: "",
    device_mod.STATUS_PENDING: "Sull'iPhone è comparsa la richiesta «Vuoi autorizzare questo computer?»: "
                               "tocca Autorizza e inserisci il codice.",
    device_mod.STATUS_LOCKED: "Sblocca il dispositivo con il codice, poi premi «Abbina».",
    device_mod.STATUS_DENIED: "Scollega e ricollega il cavo, poi tocca «Autorizza» sull'iPhone.",
    device_mod.STATUS_UNPAIRED: "Sblocca l'iPhone e premi «Abbina»: comparirà la richiesta di autorizzazione.",
    device_mod.STATUS_ERROR: "Prova a scollegare e ricollegare il cavo USB.",
}


def device_title(d: DeviceInfo) -> str:
    return d.name or "Dispositivo iOS"


def device_label(d: DeviceInfo) -> str:
    """Riga nel menu a tendina dei dispositivi."""
    parts = [device_title(d)]
    if d.product_version:
        parts.append(f"iOS {d.product_version}")
    if d.is_network:
        parts.append("Wi-Fi")
    if not d.trusted:
        parts.append("da autorizzare")
    return " · ".join(parts)


def device_status(d: DeviceInfo) -> tuple[str, str]:
    return _DEVICE_STATUS.get(d.status, _DEVICE_STATUS[device_mod.STATUS_ERROR])


def device_help(d: DeviceInfo) -> str:
    return _DEVICE_HELP.get(d.status, "")


def parse_iso(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def format_date(value) -> str:
    dt = parse_iso(value)
    if not dt:
        return ""
    return dt.astimezone().strftime("%d/%m/%Y")


def expiry_status(expires, now: datetime | None = None) -> tuple[str, str]:
    """(testo, tipo pill) per la scadenza della firma."""
    dt = parse_iso(expires)
    if not dt:
        return "Scadenza sconosciuta", "neutral"
    now = now or datetime.now(timezone.utc)
    seconds = (dt - now).total_seconds()
    if seconds <= 0:
        return "Scaduta", "error"
    hours = seconds / 3600
    if hours < 24:
        h = max(1, int(hours))
        return (f"Scade tra {h} ora" if h == 1 else f"Scade tra {h} ore"), "error"
    days = int(hours // 24)
    text = "Scade domani" if days == 1 else f"Scade tra {days} giorni"
    return text, ("warn" if days <= 2 else "ok")


def join_requirements(items: list[str]) -> str:
    """'a', 'a e b', 'a, b e c'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " e " + items[-1]
