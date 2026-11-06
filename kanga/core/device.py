"""Gestione del dispositivo iOS via pymobiledevice3.

Lista device USB/Wi-Fi, verifica pairing/trust e installazione IPA con
callback di progresso. Richiede il servizio Apple Mobile Device (usbmuxd):
su Windows viene da iTunes classico o dall'app "Apple Devices" (MS Store).

Nota API pymobiledevice3 11.x: API mista sync/async — `create_using_usbmux`
è coroutine, `install_from_local` è sync, `get_apps` è coroutine.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    pass


@dataclass
class DeviceInfo:
    udid: str
    name: str
    product_version: str = ""
    product_type: str = ""
    trusted: bool = False


def usbmux_available() -> bool:
    """True se il servizio Apple Mobile Device (usbmuxd) risponde."""
    try:
        from pymobiledevice3.usbmux import list_devices

        asyncio.run(list_devices())
        return True
    except Exception as e:
        logger.debug("usbmux non disponibile: %s", e)
        return False


def _make_lockdown(serial: str):
    from pymobiledevice3.lockdown import create_using_usbmux

    return asyncio.run(
        create_using_usbmux(serial=serial, autopair=True, pairing_records_cache_folder=None)
    )


def list_devices() -> list[DeviceInfo]:
    """Device collegati via USB (e Wi-Fi se già configurati)."""
    from pymobiledevice3.exceptions import DeviceNotFoundError, NotPairedError
    from pymobiledevice3.usbmux import list_devices as usbmux_list

    out: list[DeviceInfo] = []
    for d in asyncio.run(usbmux_list()):
        info = DeviceInfo(udid=d.serial, name=d.serial)
        try:
            lockdown = _make_lockdown(d.serial)
            ident = lockdown.all_values if isinstance(lockdown.all_values, dict) else lockdown.all_values()
            info.name = ident.get("DeviceName", d.serial)
            info.product_version = str(ident.get("ProductVersion", ""))
            info.product_type = str(ident.get("ProductType", ""))
            info.trusted = True
        except NotPairedError:
            info.trusted = False
            info.name = f"{d.serial} (da abbinare)"
        except DeviceNotFoundError:
            continue
        except Exception as e:
            logger.debug("device %s non interrogabile: %s", d.serial, e)
            info.trusted = False
        out.append(info)
    return out


def pair(serial: str) -> None:
    """Avvia il pairing: l'utente deve premere 'Autorizza' sul device."""
    try:
        lockdown = _make_lockdown(serial)
        logger.info("Pairing con %s ok (%s)", serial, lockdown.identification.get("DeviceName"))
    except Exception as e:
        raise DeviceError(
            f"Pairing fallito con {serial}: {e}. "
            "Sblocca il telefono e premi 'Autorizza' quando richiesto, poi riprova."
        ) from e


def install_ipa(serial: str, ipa_path, on_progress=None) -> None:
    """Installa un IPA sul device con callback di progresso.

    on_progress(percent: int | None, state: str) — percent None quando Apple
    non fornisce numeri (fase finale 'Verifying').
    """
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    ipa_path = str(Path(ipa_path))

    async def _run():
        lockdown = await _async_lockdown(serial)
        proxy = InstallationProxyService(lockdown)
        try:
            def handler(*args, **_kwargs):
                if on_progress and args:
                    on_progress(args[0], str(args[1]) if len(args) > 1 else "")

            proxy.install_from_local(ipa_path, handler=handler)
        finally:
            try:
                proxy.close()
            except Exception:
                pass

    try:
        asyncio.run(_run())
    except Exception as e:
        raise DeviceError(f"Installazione fallita: {e}") from e


async def _async_lockdown(serial: str):
    from pymobiledevice3.lockdown import create_using_usbmux

    return await create_using_usbmux(serial=serial, autopair=True, pairing_records_cache_folder=None)


def installed_apps(serial: str) -> dict[str, dict]:
    """Elenco app installate (per gestire il limite di 3 app free)."""
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    async def _run():
        lockdown = await _async_lockdown(serial)
        proxy = InstallationProxyService(lockdown)
        try:
            return await proxy.get_apps()
        finally:
            try:
                proxy.close()
            except Exception:
                pass

    try:
        return asyncio.run(_run())
    except Exception as e:
        raise DeviceError(f"Lettura app installate fallita: {e}") from e
