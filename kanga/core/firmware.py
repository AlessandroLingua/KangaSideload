"""Firmware iOS: versioni firmate da Apple, download IPSW, SHSH e ripristino.

Regola di Apple (vale per iTunes, Finder, 3uTools e qualunque altro tool):
si può installare SOLO una versione di iOS che Apple sta ancora firmando
(server TSS). Di solito è l'ultima; dopo un aggiornamento Apple continua a
firmare la precedente per qualche giorno, ed è quella la finestra per un
downgrade. Le versioni non firmate non si installano senza SHSH salvati prima
(e strumenti come futurerestore): per questo Kanga può salvare gli SHSH delle
versioni ancora firmate.

Fonti:
- elenco firmware e stato di firma: api.ipsw.me (gli URL puntano ai server Apple)
- IPSW: scaricato dai server Apple, verificato con SHA-256 (o SHA-1)
- SHSH e ripristino: pymobiledevice3 (TSS + restore). Il ripristino diretto su
  Windows richiede libusb-1.0.dll e il driver WinUSB per la modalità Recovery;
  in alternativa l'IPSW scaricato si installa con iTunes (Maiusc + «Ripristina»).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import plistlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import store

logger = logging.getLogger(__name__)

IPSWME_API = "https://api.ipsw.me/v4/device/{identifier}?type=ipsw"
IPSWME_DEVICES = "https://api.ipsw.me/v4/devices"
IOS_FAMILIES = ("iPhone", "iPad", "iPod")  # dispositivi con iOS / iPadOS
CHUNK = 1024 * 1024

# relazione tra una versione e quella installata
DOWNGRADE, CURRENT, UPGRADE = "downgrade", "current", "upgrade"


class FirmwareError(RuntimeError):
    pass


class DownloadCancelled(FirmwareError):
    pass


@dataclass(frozen=True)
class Firmware:
    identifier: str
    version: str
    buildid: str
    url: str
    size: int = 0
    sha256: str = ""
    sha1: str = ""
    signed: bool = False
    released: str = ""

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1].split("?")[0] or f"{self.identifier}_{self.version}_{self.buildid}.ipsw"

    @property
    def label(self) -> str:
        return f"iOS {self.version} ({self.buildid})"


@dataclass(frozen=True)
class Model:
    identifier: str  # es. iPhone9,3
    name: str        # es. iPhone 7 (GSM)

    @property
    def family(self) -> str:
        return re.match(r"[A-Za-z]+", self.identifier).group(0)


@dataclass
class DeviceFirmwareInfo:
    udid: str
    product_type: str
    hardware_model: str = ""
    version: str = ""
    build: str = ""
    ecid: int = 0
    has_baseband: bool = True  # iPad Wi-Fi e iPod touch non hanno modem

    @property
    def ecid_hex(self) -> str:
        return f"0x{self.ecid:X}" if self.ecid else ""


# ------------------------------------------------------------------ versioni
def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version or "")[:4]) or (0,)


def relation(fw: Firmware, current_version: str, current_build: str = "") -> str:
    if current_build and fw.buildid == current_build:
        return CURRENT
    a, b = version_key(fw.version), version_key(current_version)
    if a == b:
        return CURRENT
    return DOWNGRADE if a < b else UPGRADE


def parse_firmwares(data: dict) -> list[Firmware]:
    out = []
    for f in data.get("firmwares") or []:
        if not f.get("url"):
            continue
        out.append(Firmware(
            identifier=str(f.get("identifier") or data.get("identifier") or ""),
            version=str(f.get("version") or ""),
            buildid=str(f.get("buildid") or ""),
            url=str(f["url"]),
            size=int(f.get("filesize") or 0),
            sha256=str(f.get("sha256sum") or ""),
            sha1=str(f.get("sha1sum") or ""),
            signed=bool(f.get("signed")),
            released=str(f.get("releasedate") or "")[:10],
        ))
    out.sort(key=lambda fw: (version_key(fw.version), fw.buildid), reverse=True)
    return out


def list_firmwares(product_type: str) -> list[Firmware]:
    """Tutte le versioni di iOS per il modello, dalla più recente, con lo stato di firma attuale."""
    if not product_type:
        raise FirmwareError("Modello del dispositivo sconosciuto")
    try:
        r = requests.get(IPSWME_API.format(identifier=product_type), timeout=30,
                         headers={"Accept": "application/json"})
        r.raise_for_status()
        return parse_firmwares(r.json())
    except requests.RequestException as e:
        raise FirmwareError(f"Elenco dei firmware non disponibile (api.ipsw.me): {e}") from e


def parse_models(data: list) -> list[Model]:
    """Solo iPhone, iPad e iPod touch, per famiglia e dal più recente."""
    models = [Model(str(d["identifier"]), str(d.get("name") or d["identifier"]))
              for d in data or [] if str(d.get("identifier", "")).startswith(IOS_FAMILIES)]
    order = {f: i for i, f in enumerate(IOS_FAMILIES)}
    models.sort(key=lambda m: (order.get(m.family, 9), tuple(-n for n in version_key(m.identifier))))
    return models


_models_cache: list[Model] | None = None


def list_models() -> list[Model]:
    """Tutti i modelli Apple con iOS/iPadOS conosciuti da ipsw.me (anche quelli non collegati)."""
    global _models_cache
    if _models_cache is None:
        try:
            r = requests.get(IPSWME_DEVICES, timeout=30, headers={"Accept": "application/json"})
            r.raise_for_status()
            _models_cache = parse_models(r.json())
        except requests.RequestException as e:
            raise FirmwareError(f"Elenco dei modelli non disponibile (api.ipsw.me): {e}") from e
    return list(_models_cache)


# ---------------------------------------------------------------- cartelle
def ipsw_dir() -> Path:
    return store.base_dir() / "ipsw"


def shsh_dir() -> Path:
    return store.base_dir() / "shsh"


def local_ipsw(fw: Firmware, dest_dir: Path | None = None) -> Path | None:
    """IPSW già scaricato e completo (verificato al termine del download)."""
    path = Path(dest_dir or ipsw_dir()) / fw.filename
    if path.is_file() and (not fw.size or path.stat().st_size == fw.size):
        return path
    return None


# ---------------------------------------------------------------- download
def _hash_of(path: Path, algo: str, should_stop) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if should_stop and should_stop():
                raise DownloadCancelled("Verifica annullata")
            h.update(chunk)
    return h.hexdigest()


def download_ipsw(fw: Firmware, progress_cb=None, should_stop=None, dest_dir: Path | None = None,
                  session=None) -> Path:
    """Scarica l'IPSW dai server Apple, riprendendo un download interrotto, e lo verifica.

    progress_cb(scaricati, totale) · should_stop() -> True per annullare (il file
    parziale resta e il prossimo download riparte da lì).
    """
    dest_dir = Path(dest_dir or ipsw_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    done_path = local_ipsw(fw, dest_dir)
    if done_path:
        return done_path
    final = dest_dir / fw.filename
    part = final.with_name(final.name + ".part")
    http = session or requests
    start = part.stat().st_size if part.exists() else 0
    if fw.size and start > fw.size:
        part.unlink()
        start = 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    logger.info("Scarico %s (%s byte già presenti)", fw.url, start)
    try:
        with http.get(fw.url, stream=True, timeout=60, headers=headers) as r:
            if start and r.status_code == 200:
                start = 0  # il server non supporta la ripresa: si ricomincia
            elif r.status_code == 416:
                pass  # già tutto scaricato
            else:
                r.raise_for_status()
            total = fw.size or (start + int(r.headers.get("Content-Length") or 0))
            done = start
            if r.status_code != 416:
                with open(part, "ab" if start else "wb") as f:
                    for chunk in r.iter_content(chunk_size=CHUNK):
                        if should_stop and should_stop():
                            raise DownloadCancelled("Download annullato: riprendilo quando vuoi")
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if progress_cb:
                                progress_cb(done, total)
    except requests.RequestException as e:
        raise FirmwareError(f"Download dell'IPSW interrotto ({e}): riprova per riprendere da dove era") from e

    size = part.stat().st_size
    if fw.size and size != fw.size:
        raise FirmwareError(f"IPSW incompleto ({size} byte su {fw.size}): riprova per completarlo")
    algo, expected = ("sha256", fw.sha256) if fw.sha256 else ("sha1", fw.sha1)
    if expected:
        if progress_cb:
            progress_cb(-1, total)  # -1 = verifica in corso
        if _hash_of(part, algo, should_stop) != expected.lower():
            part.unlink(missing_ok=True)
            raise FirmwareError(f"L'IPSW scaricato non corrisponde al suo {algo.upper()}: file scartato")
    part.replace(final)
    return final


# ------------------------------------------------------------------ dispositivo
async def _lockdown(udid: str):
    from pymobiledevice3.lockdown import create_using_usbmux

    return await create_using_usbmux(serial=udid, connection_type="USB")


def read_device_info(udid: str) -> DeviceFirmwareInfo:
    """Modello, board, versione/build e ECID (serve il collegamento via cavo USB)."""
    from .device import _close, _values

    async def run():
        lockdown = await _lockdown(udid)
        try:
            v = _values(lockdown)
        finally:
            await _close(lockdown)
        return DeviceFirmwareInfo(
            udid=udid,
            product_type=str(v.get("ProductType") or ""),
            hardware_model=str(v.get("HardwareModel") or ""),
            version=str(v.get("ProductVersion") or ""),
            build=str(v.get("BuildVersion") or ""),
            ecid=int(v.get("UniqueChipID") or 0),
            has_baseband=bool(v.get("BasebandVersion")),
        )

    try:
        return asyncio.run(run())
    except Exception as e:
        raise FirmwareError(f"Dispositivo non leggibile via USB: {e}") from e


# ------------------------------------------------------------------------ SHSH
def shsh_path(info: DeviceFirmwareInfo, fw: Firmware) -> Path:
    return shsh_dir() / (info.ecid_hex or info.udid) / f"{fw.identifier}_{fw.version}_{fw.buildid}.shsh2"


def local_shsh(fw: Firmware, ecid: int = 0) -> Path | None:
    """SHSH già salvati per questa versione (del dispositivo con questo ECID, se noto)."""
    name = f"{fw.identifier}_{fw.version}_{fw.buildid}.shsh2"
    base = shsh_dir()
    if ecid:
        path = base / f"0x{ecid:X}" / name
        return path if path.is_file() else None
    return next(iter(sorted(base.glob(f"*/{name}"))), None) if base.is_dir() else None


def save_shsh(info: DeviceFirmwareInfo, fw: Firmware) -> Path:
    """Chiede ad Apple gli SHSH (ticket TSS) di una versione firmata e li salva.

    Dall'IPSW legge solo il BuildManifest, direttamente dal server Apple (niente
    download completo). Gli SHSH valgono finché il nonce del dispositivo non
    cambia: servono a strumenti come futurerestore per downgrade futuri.
    """
    if not fw.signed:
        raise FirmwareError(f"{fw.label} non è più firmata da Apple: SHSH non ottenibili")
    from ipsw_parser.ipsw import IPSW
    from pymobiledevice3.restore.device import Device
    from pymobiledevice3.restore.recovery import Behavior, Recovery

    from .device import _close

    async def run():
        lockdown = await _lockdown(info.udid)
        try:
            ipsw = IPSW.create_from_path(fw.url)
            return await Recovery(ipsw, Device(lockdown=lockdown), behavior=Behavior.Erase).fetch_tss_record()
        finally:
            await _close(lockdown)

    try:
        tss = asyncio.run(run())
    except Exception as e:
        raise FirmwareError(f"Richiesta SHSH non riuscita: {e}") from e
    path = shsh_path(info, fw)
    store.write_private(path, plistlib.dumps(dict(tss)))
    logger.info("SHSH salvati in %s", path)
    return path


# ------------------------------------------------------------------ ripristino
def libusb_dir() -> Path:
    """Dove mettere libusb-1.0.dll (in alternativa: accanto a python.exe o nel PATH)."""
    return store.base_dir() / "libusb"


def libusb_available() -> bool:
    """libusb serve a pymobiledevice3 per parlare con l'iPhone in modalità Recovery/DFU."""
    if os.name != "nt":
        return True
    d = libusb_dir()
    if (d / "libusb-1.0.dll").is_file():
        os.add_dll_directory(str(d))
        if str(d) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    try:
        from pymobiledevice3.usb import list_devices

        list_devices()
        return True
    except Exception as e:
        logger.debug("libusb non disponibile: %s", e)
        return False


def restore(udid: str, ipsw_path: Path, erase: bool = True) -> None:
    """Ripristina (erase=True, cancella tutto) o aggiorna l'iOS con l'IPSW dato.

    Apple controlla la firma: se la versione non è firmata il ripristino fallisce
    prima di modificare il sistema, ma il dispositivo può restare in modalità
    Recovery (si esce ripristinando una versione firmata, anche con iTunes).
    """
    from ipsw_parser.ipsw import IPSW
    from pymobiledevice3.restore.device import Device
    from pymobiledevice3.restore.recovery import Behavior
    from pymobiledevice3.restore.restore import Restore

    if not libusb_available():
        raise FirmwareError(
            "Ripristino diretto non disponibile: manca libusb-1.0.dll "
            f"(mettila in {libusb_dir()}). In alternativa usa il ripristino con iTunes.")
    ipsw_path = Path(ipsw_path)

    async def run():
        lockdown = await _lockdown(udid)
        ipsw = IPSW.create_from_path(str(ipsw_path))
        behavior = Behavior.Erase if erase else Behavior.Update
        await Restore(ipsw, Device(lockdown=lockdown), behavior=behavior).update()

    try:
        asyncio.run(run())
    except Exception as e:
        raise FirmwareError(f"Ripristino non riuscito: {e}") from e


ITUNES_PATHS = (
    r"C:\Program Files\iTunes\iTunes.exe",
    r"C:\Program Files (x86)\iTunes\iTunes.exe",
)


def itunes_exe() -> Path | None:
    return next((Path(p) for p in ITUNES_PATHS if Path(p).is_file()), None)


# ------------------------------------------------------------- futurerestore
# futurerestore installa una versione NON più firmata usando SHSH salvati quando
# lo era. Non è incluso né scaricato da Kanga (la build per Windows è distribuita
# fuori da GitHub): l'utente sceglie il proprio futurerestore.exe.
FUTURERESTORE_SITE = "https://github.com/m1stadev/futurerestore/releases"


def _futurerestore_config() -> Path:
    return store.base_dir() / "futurerestore.json"


def find_futurerestore() -> Path | None:
    """futurerestore scelto dall'utente (KANGA_FUTURERESTORE, impostazione salvata,
    %LOCALAPPDATA%/Kanga/futurerestore/ o PATH)."""
    candidates = [os.environ.get("KANGA_FUTURERESTORE", "")]
    try:
        candidates.append(json.loads(_futurerestore_config().read_text(encoding="utf-8")).get("path", ""))
    except Exception:
        pass
    exe = "futurerestore.exe" if os.name == "nt" else "futurerestore"
    candidates += [str(store.base_dir() / "futurerestore" / exe), shutil.which("futurerestore") or ""]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def set_futurerestore(path) -> Path:
    path = Path(path)
    if not path.is_file():
        raise FirmwareError(f"File non trovato: {path}")
    store.base_dir().mkdir(parents=True, exist_ok=True)
    _futurerestore_config().write_text(json.dumps({"path": str(path)}), encoding="utf-8")
    return path


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def futurerestore_version(path) -> str:
    """Prima riga utile dell'help di futurerestore (versione), "" se non risponde."""
    try:
        proc = subprocess.run([str(path), "--help"], capture_output=True, text=True, timeout=20,
                              errors="replace", creationflags=_no_window())
    except Exception as e:
        logger.debug("futurerestore non eseguibile: %s", e)
        return ""
    output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return next((ln for ln in lines if "version" in ln.lower()), lines[0] if lines else "")


@dataclass(frozen=True)
class BlobInfo:
    path: Path
    ecid: int = 0
    ap_nonce: bytes = b""
    chip_id: int | None = None
    board_id: int | None = None
    generator: str = ""

    @property
    def ecid_hex(self) -> str:
        return f"0x{self.ecid:X}" if self.ecid else "?"


def read_blob(path) -> BlobInfo:
    """Legge un file SHSH (.shsh2/.shsh/.plist con ApImg4Ticket): ECID, ApNonce, generator."""
    from pyimg4 import IM4M

    path = Path(path)
    try:
        data = plistlib.loads(path.read_bytes())
        ticket = IM4M(bytes(data["ApImg4Ticket"]))
        return BlobInfo(path=path, ecid=int(ticket.ecid or 0), ap_nonce=bytes(ticket.apnonce or b""),
                        chip_id=ticket.chip_id, board_id=ticket.board_id,
                        generator=str(data.get("generator") or ""))
    except Exception as e:
        raise FirmwareError(f"File SHSH non valido ({path.name}): {e}") from e


def read_ap_nonce(udid: str) -> bytes:
    """ApNonce attuale del dispositivo (deve coincidere con quello degli SHSH)."""
    from pymobiledevice3.restore.device import Device

    from .device import _close

    async def run():
        lockdown = await _lockdown(udid)
        try:
            return bytes(await Device(lockdown=lockdown).get_ap_nonce() or b"")
        finally:
            await _close(lockdown)

    try:
        return asyncio.run(run())
    except Exception as e:
        raise FirmwareError(f"Nonce del dispositivo non leggibile: {e}") from e


@dataclass
class BlobCheck:
    problems: list[str] = field(default_factory=list)  # impediscono il ripristino
    warnings: list[str] = field(default_factory=list)  # probabile fallimento, ma si può tentare
    nonce_ok: bool | None = None


def check_blob(info: DeviceFirmwareInfo, blob: BlobInfo, ap_nonce: bytes | None) -> BlobCheck:
    check = BlobCheck()
    if not blob.ecid:
        check.problems.append("Gli SHSH non contengono l'ECID: il file non è utilizzabile.")
    elif info.ecid and blob.ecid != info.ecid:
        check.problems.append(f"Gli SHSH sono di un altro dispositivo (ECID {blob.ecid_hex}, "
                              f"questo è {info.ecid_hex}).")
    if ap_nonce is None or not blob.ap_nonce:
        check.warnings.append("Impossibile confrontare il nonce del dispositivo con quello degli SHSH.")
    elif ap_nonce == blob.ap_nonce:
        check.nonce_ok = True
    else:
        check.nonce_ok = False
        gen = f" (generator degli SHSH: {blob.generator})" if blob.generator else ""
        check.warnings.append(
            "Il nonce attuale del dispositivo è diverso da quello degli SHSH: futurerestore funziona "
            f"solo se prima si imposta il nonce al generator degli SHSH{gen}, con un jailbreak oppure "
            "con il dispositivo in pwned DFU (chip A7-A11). Altrimenti il ripristino fallirà.")
    return check


def futurerestore_args(exe, blob: Path, ipsw: Path, has_baseband: bool = True,
                       pwndfu: bool = False) -> list[str]:
    args = [str(exe), "-t", str(blob), "--latest-sep",
            "--latest-baseband" if has_baseband else "--no-baseband"]
    if pwndfu:
        args += ["--use-pwndfu", "--set-nonce"]
    return args + [str(ipsw)]


def run_futurerestore(args: list[str], on_line=None) -> None:
    """Esegue futurerestore riportando l'output riga per riga (può durare 10-20 minuti)."""
    logger.info("Avvio: %s", " ".join(args))
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=_no_window())
    except OSError as e:
        raise FirmwareError(f"futurerestore non avviabile: {e}") from e
    tail: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        tail = (tail + [line])[-6:]
        if on_line:
            on_line(line)
    rc = proc.wait()
    if rc != 0:
        raise FirmwareError(f"futurerestore terminato con errore (codice {rc}): " + " | ".join(tail[-3:]))


def exit_recovery(exe) -> None:
    """Fa uscire il dispositivo dalla modalità Recovery (es. dopo un ripristino fallito)."""
    run_futurerestore([str(exe), "--exit-recovery"])

