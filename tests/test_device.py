"""Modulo dispositivo con pymobiledevice3 simulato (API sync e async)."""
import pytest

from kanga.core import device


class FakeLockdown:
    def __init__(self, values):
        self.all_values = values
        self.closed = False

    async def close(self):
        self.closed = True


class MuxDev:
    def __init__(self, serial, connection_type="USB"):
        self.serial = serial
        self.connection_type = connection_type


def _exc(name):
    return type(name, (Exception,), {})("simulato")


@pytest.fixture
def usbmux(monkeypatch):
    import pymobiledevice3.usbmux as mux

    state = {"devices": []}

    async def fake_list(*_a, **_k):
        return state["devices"]

    monkeypatch.setattr(mux, "list_devices", fake_list)
    return state


def test_status_from_exception():
    assert device.status_from_exception(_exc("PairingDialogResponsePendingError")) == device.STATUS_PENDING
    assert device.status_from_exception(_exc("PasswordRequiredError")) == device.STATUS_LOCKED
    assert device.status_from_exception(_exc("UserDeniedPairingError")) == device.STATUS_DENIED
    assert device.status_from_exception(_exc("NotPairedError")) == device.STATUS_UNPAIRED
    assert device.status_from_exception(ValueError()) == device.STATUS_ERROR


def test_list_devices_statuses_and_dedupe(monkeypatch, usbmux):
    usbmux["devices"] = [
        MuxDev("AAA"), MuxDev("AAA", "Network"),  # stesso iPhone via USB e Wi-Fi
        MuxDev("BBB"), MuxDev("CCC"), MuxDev("GONE"),
    ]
    lockdowns = []

    async def fake_lockdown(serial, autopair=True):
        if serial == "AAA":
            ld = FakeLockdown({"DeviceName": "iPhone di Mario", "ProductVersion": "18.1",
                               "ProductType": "iPhone15,2"})
            lockdowns.append(ld)
            return ld
        if serial == "BBB":
            raise _exc("PairingDialogResponsePendingError")
        if serial == "GONE":
            raise _exc("DeviceNotFoundError")
        raise _exc("PasswordRequiredError")

    monkeypatch.setattr(device, "_async_lockdown", fake_lockdown)
    devs = {d.udid: d for d in device.list_devices()}
    assert set(devs) == {"AAA", "BBB", "CCC"}
    a = devs["AAA"]
    assert (a.name, a.product_version, a.trusted, a.status, a.connection) == (
        "iPhone di Mario", "18.1", True, "ok", "USB")
    assert devs["BBB"].status == "pending" and not devs["BBB"].trusted
    assert devs["CCC"].status == "locked"
    assert lockdowns and all(ld.closed for ld in lockdowns)  # connessioni chiuse


def test_list_serials(usbmux):
    usbmux["devices"] = [MuxDev("B"), MuxDev("A"), MuxDev("A", "Network")]
    assert device.list_serials() == ["A", "B"]


def test_pair_uses_lockdown_values(monkeypatch):
    async def fake_lockdown(serial, autopair=True):
        return FakeLockdown({"DeviceName": "iPad", "ProductVersion": "17.0"})

    monkeypatch.setattr(device, "_async_lockdown", fake_lockdown)
    info = device.pair("XYZ")  # prima falliva sempre: usava un attributo inesistente
    assert info.trusted and info.name == "iPad"

    async def failing(serial, autopair=True):
        raise _exc("UserDeniedPairingError")

    monkeypatch.setattr(device, "_async_lockdown", failing)
    with pytest.raises(device.DeviceError, match="Autorizza"):
        device.pair("XYZ")


@pytest.mark.parametrize("async_api", [True, False])
def test_install_ipa_awaits_async_api(monkeypatch, tmp_path, async_api):
    """Regressione: con pymobiledevice3 11.50 install_from_local è una coroutine e
    non veniva mai attesa, quindi l'app non veniva installata."""
    import pymobiledevice3.services.installation_proxy as ip

    calls = {}
    ld = FakeLockdown({})

    class FakeProxy:
        def __init__(self, lockdown=None):
            calls["lockdown"] = lockdown
            self.closed = False

        def _install(self, path, handler=None):
            calls["path"] = path
            handler(30)
            handler(90, "Installing")
            calls["installed"] = True

        if async_api:
            async def install_from_local(self, path, handler=None):
                self._install(path, handler)

            async def close(self):
                calls["closed"] = True
        else:
            def install_from_local(self, path, handler=None):
                self._install(path, handler)

            def close(self):
                calls["closed"] = True

    async def fake_lockdown(serial, autopair=True):
        return ld

    monkeypatch.setattr(ip, "InstallationProxyService", FakeProxy)
    monkeypatch.setattr(device, "_async_lockdown", fake_lockdown)
    progress = []
    ipa = tmp_path / "a.ipa"
    ipa.write_bytes(b"x")
    device.install_ipa("SERIAL", ipa, on_progress=lambda p, s: progress.append((p, s)))
    assert calls.get("installed") is True
    assert calls["closed"] and ld.closed
    assert progress == [(None, "Caricamento sul dispositivo"), (30, ""), (90, "Installing"), (100, "Completata")]


def test_install_ipa_wraps_errors(monkeypatch, tmp_path):
    async def failing(serial, autopair=True):
        raise RuntimeError("cavo scollegato")

    monkeypatch.setattr(device, "_async_lockdown", failing)
    with pytest.raises(device.DeviceError, match="Installazione fallita: cavo scollegato"):
        device.install_ipa("S", tmp_path / "x.ipa")
