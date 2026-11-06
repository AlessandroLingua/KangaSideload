"""Gestione del dispositivo iOS via pymobiledevice3.

Lista device USB/Wi-Fi, verifica pairing/trust e installazione IPA con
callback di progresso. Richiede il servizio Apple Mobile Device (usbmuxd):
su Windows viene da iTunes classico o dall'app "Apple Devices" (MS Store).

pymobiledevice3 è passata gradualmente da API sync ad async (in 11.50
`install_from_local`, `get_apps` e `close` sono coroutine): qui ogni chiamata
passa da `_maybe_await`, così il modulo funziona con entrambe le varianti.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    pass


# stati del dispositivo mostrati nella GUI
STATUS_OK = "ok"
STATUS_PENDING = "pending"     # in attesa di «Autorizza» sul telefono
STATUS_LOCKED = "locked"       # telefono bloccato con codice
STATUS_DENIED = "denied"       # l'utente ha rifiutato l'autorizzazione
STATUS_UNPAIRED = "unpaired"   # mai abbinato
STATUS_ERROR = "error"         # non interrogabile

_STATUS_BY_EXCEPTION = {
    "PairingDialogResponsePendingError": STATUS_PENDING,
    "PasswordRequiredError": STATUS_LOCKED,
    "UserDeniedPairingError": STATUS_DENIED,
    "NotPairedError": STATUS_UNPAIRED,
}


@dataclass
class DeviceInfo:
    udid: str
    name: str
    product_version: str = ""
    product_type: str = ""
    trusted: bool = False
    status: str = STATUS_UNPAIRED
    connection: str = "USB"

    @property
    def display_name(self) -> str:
        return self.name or self.udid

    @property
    def is_network(self) -> bool:
        return self.connection.lower() == "network"


def status_from_exception(exc: BaseException) -> str:
    for cls in type(exc).__mro__:
        if cls.__name__ in _STATUS_BY_EXCEPTION:
            return _STATUS_BY_EXCEPTION[cls.__name__]
    return STATUS_ERROR


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _close(obj) -> None:
    close = getattr(obj, "close", None)
    if close is None:
        return
    try:
        await _maybe_await(close())
    except Exception:
        pass


def usbmux_available() -> bool:
    """True se il servizio Apple Mobile Device (usbmuxd) risponde."""
    try:
        from pymobiledevice3.usbmux import list_devices

        asyncio.run(_maybe_await(list_devices()))
        return True
    except Exception as e:
        logger.debug("usbmux non disponibile: %s", e)
        return False


def list_serials() -> list[str]:
    """Solo i seriali collegati (veloce, nessun pairing): usato per il polling."""
    from pymobiledevice3.usbmux import list_devices as usbmux_list

    try:
        devices = asyncio.run(_maybe_await(usbmux_list()))
    except Exception as e:
        raise DeviceError(f"Servizio Apple Mobile Device non raggiungibile: {e}") from e
    return sorted({d.serial for d in devices})


async def _async_lockdown(serial: str, autopair: bool = True):
    from pymobiledevice3.lockdown import create_using_usbmux

    return await create_using_usbmux(
        serial=serial, autopair=autopair, pairing_records_cache_folder=None
    )


def _values(lockdown) -> dict:
    values = getattr(lockdown, "all_values", None)
    if callable(values):
        values = values()
    return values if isinstance(values, dict) else {}


def list_devices() -> list[DeviceInfo]:
    """Device collegati via USB (e Wi-Fi se già configurati)."""
    from pymobiledevice3.usbmux import list_devices as usbmux_list

    async def _run() -> list[DeviceInfo]:
        out: dict[str, DeviceInfo] = {}
        for d in await _maybe_await(usbmux_list()):
            conn = str(getattr(d, "connection_type", "USB") or "USB")
            prev = out.get(d.serial)
            if prev is not None and prev.trusted:
                continue  # stesso device via USB e Wi-Fi: basta una voce
            info = DeviceInfo(udid=d.serial, name="", connection=conn)
            try:
                lockdown = await _async_lockdown(d.serial)
                try:
                    ident = _values(lockdown)
                finally:
                    await _close(lockdown)
                info.name = str(ident.get("DeviceName") or "")
                info.product_version = str(ident.get("ProductVersion", ""))
                info.product_type = str(ident.get("ProductType", ""))
                info.trusted = True
                info.status = STATUS_OK
            except Exception as e:
                if type(e).__name__ == "DeviceNotFoundError":
                    continue
                info.status = status_from_exception(e)
                logger.debug("device %s non interrogabile: %s", d.serial, e)
            out[d.serial] = info
        return list(out.values())

    return asyncio.run(_run())


def pair(serial: str) -> DeviceInfo:
    """Avvia il pairing: l'utente deve premere 'Autorizza' sul device."""

    async def _run() -> DeviceInfo:
        lockdown = await _async_lockdown(serial)
        try:
            ident = _values(lockdown)
        finally:
            await _close(lockdown)
        return DeviceInfo(
            udid=serial,
            name=str(ident.get("DeviceName") or ""),
            product_version=str(ident.get("ProductVersion", "")),
            product_type=str(ident.get("ProductType", "")),
            trusted=True,
            status=STATUS_OK,
        )

    try:
        info = asyncio.run(_run())
    except Exception as e:
        raise DeviceError(
            f"Pairing fallito con {serial}: {e}. "
            "Sblocca il telefono e premi 'Autorizza' quando richiesto, poi riprova."
        ) from e
    logger.info("Pairing con %s ok (%s)", serial, info.name)
    return info


def install_ipa(serial: str, ipa_path, on_progress=None) -> None:
    """Installa un IPA sul device con callback di progresso.

    on_progress(percent: int | None, state: str) — percent None durante il
    caricamento del file sul dispositivo (Apple non fornisce numeri).
    """
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    ipa_path = Path(ipa_path)

    def report(pct, state: str) -> None:
        if on_progress:
            try:
                on_progress(None if pct is None else int(pct), state)
            except Exception:
                logger.debug("callback di progresso fallita", exc_info=True)

    async def _run():
        lockdown = await _async_lockdown(serial)
        proxy = InstallationProxyService(lockdown=lockdown)
        try:
            def handler(percent, *args, **_kwargs):
                state = str(args[0]) if args else ""
                report(percent, state)

            report(None, "Caricamento sul dispositivo")
            await _maybe_await(proxy.install_from_local(ipa_path, handler=handler))
            report(100, "Completata")
        finally:
            await _close(proxy)
            await _close(lockdown)

    try:
        asyncio.run(_run())
    except Exception as e:
        raise DeviceError(f"Installazione fallita: {e}") from e


def installed_apps(serial: str) -> dict[str, dict]:
    """Elenco app installate (per gestire il limite di 3 app free)."""
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    async def _run():
        lockdown = await _async_lockdown(serial)
        proxy = InstallationProxyService(lockdown=lockdown)
        try:
            return await _maybe_await(proxy.get_apps())
        finally:
            await _close(proxy)
            await _close(lockdown)

    try:
        return asyncio.run(_run())
    except Exception as e:
        raise DeviceError(f"Lettura app installate fallita: {e}") from e
