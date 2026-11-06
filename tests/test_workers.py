"""Worker Qt: consegna dei risultati nel thread principale e login con 2FA."""
import threading

from PySide6.QtCore import QThread

from fakes import FakeFlow
from kanga.core.gsa import GsaError
from kanga.gui.workers import FlowBridge, LoginWorker, start_worker, wait_all


def test_worker_result_delivered_on_main_thread(qtbot, qapp):
    seen = {}

    def work(x):
        seen["work_thread"] = threading.current_thread()
        return x * 2

    def ok(result):
        seen["result"] = result
        seen["ok_thread"] = QThread.currentThread()

    start_worker(work, 21, on_ok=ok)
    qtbot.waitUntil(lambda: "result" in seen, timeout=3000)
    assert seen["result"] == 42
    assert seen["work_thread"] is not threading.main_thread()
    assert seen["ok_thread"] is qapp.thread()  # i widget si possono toccare in sicurezza


def test_worker_failure_delivers_exception(qtbot, qapp):
    seen = {}

    def boom():
        raise GsaError("no", apple_code=-1)

    start_worker(boom, on_fail=lambda e: seen.update(exc=e, thread=QThread.currentThread()))
    qtbot.waitUntil(lambda: "exc" in seen, timeout=3000)
    assert isinstance(seen["exc"], GsaError) and seen["exc"].apple_code == -1
    assert seen["thread"] is qapp.thread()


def test_flow_bridge_marshals_events(qtbot, qapp):
    received = []
    bridge = FlowBridge(lambda tag, data: received.append((tag, data, QThread.currentThread())))
    t = threading.Thread(target=lambda: bridge.emit("progress", (50, "Installing")))
    t.start()
    t.join()
    qtbot.waitUntil(lambda: bool(received), timeout=3000)
    assert received[0][:2] == ("progress", (50, "Installing"))
    assert received[0][2] is qapp.thread()


def _start_login(flow):
    w = LoginWorker(flow, "mario@icloud.com", "pw")
    events = []
    w.need_2fa.connect(lambda: events.append("need_2fa"))
    w.code_rejected.connect(lambda m: events.append(("rejected", m)))
    w.succeeded.connect(lambda s: events.append("ok"))
    w.failed.connect(lambda e: events.append(("failed", e)))
    w.cancelled.connect(lambda: events.append("cancelled"))
    w.start()
    return w, events


def test_login_without_2fa(qtbot):
    flow = FakeFlow()
    w, events = _start_login(flow)
    qtbot.waitUntil(lambda: "ok" in events, timeout=3000)
    assert flow.login_calls == [("mario@icloud.com", "pw")]
    assert w._password == ""  # la password non resta in memoria nel worker


def test_login_2fa_wrong_code_then_resend_then_right(qtbot):
    flow = FakeFlow(require_2fa=True)
    w, events = _start_login(flow)
    qtbot.waitUntil(lambda: "need_2fa" in events, timeout=3000)
    w.submit_code("000000")
    qtbot.waitUntil(lambda: any(isinstance(e, tuple) and e[0] == "rejected" for e in events), timeout=3000)
    assert "ok" not in events  # un codice errato non fa fallire il login
    w.resend_code()
    w.submit_code("123456")
    qtbot.waitUntil(lambda: "ok" in events, timeout=3000)
    assert flow.gsa.triggered == 2
    assert flow.gsa.submitted == ["000000", "123456"]
    assert flow.logged_in


def test_login_cancel_during_2fa(qtbot):
    flow = FakeFlow(require_2fa=True)
    w, events = _start_login(flow)
    qtbot.waitUntil(lambda: "need_2fa" in events, timeout=3000)
    w.cancel()
    qtbot.waitUntil(lambda: "cancelled" in events, timeout=3000)
    assert not flow.logged_in and "ok" not in events


def test_login_failure_reported(qtbot):
    flow = FakeFlow(login_error=GsaError("Login fallito: -22406"))
    w, events = _start_login(flow)
    qtbot.waitUntil(lambda: any(isinstance(e, tuple) and e[0] == "failed" for e in events), timeout=3000)
    assert wait_all(3000)


def test_cancel_while_request_in_flight_discards_session(qtbot):
    gate = threading.Event()

    class SlowFlow(FakeFlow):
        def login(self, apple_id, password, on_2fa=None):
            gate.wait(3)
            return super().login(apple_id, password, on_2fa)

    flow = SlowFlow()
    w, events = _start_login(flow)
    w.cancel()
    gate.set()
    qtbot.waitUntil(lambda: "cancelled" in events, timeout=3000)
    assert not flow.logged_in and "ok" not in events
