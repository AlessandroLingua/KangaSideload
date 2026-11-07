"""Richieste GrandSlam: connessione nuova a ogni richiesta e retry di apptokens sui 5xx."""
import os
import plistlib

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kanga.core import gsa
from kanga.core.errors import friendly_error
from kanga.core.gsa import GsaClient, GsaError

SK = bytes(range(32))
SPD = {"sk": SK, "adsid": "000307-0-ABC", "c": b"c-blob", "GsIdmsToken": "idms"}
APP = "com.apple.gs.xcode.auth"


class Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.content = plistlib.dumps({"Response": payload or {}}) if status == 200 else b"<html>503</html>"


def token_reply(token="XCODE-TOKEN"):
    iv = os.urandom(16)
    plain = plistlib.dumps({"t": {APP: {"token": token}}})
    et = b"XYZ" + iv + AESGCM(SK).encrypt(iv, plain, b"XYZ")
    return Resp(200, {"et": et, "Status": {"ec": 0}})


@pytest.fixture
def client(monkeypatch):
    c = GsaClient(lambda: {"X-Apple-I-MD": "otp"})
    c.sent = []
    c.waits = []
    c.replies = []
    c._sleep = c.waits.append

    def post(url, headers=None, data=None, timeout=None):
        c.sent.append((headers, plistlib.loads(data)["Request"]))
        return c.replies.pop(0)

    monkeypatch.setattr(c._session, "post", post)
    return c


def test_every_request_uses_a_new_connection(client):
    client.replies = [token_reply()]
    client.fetch_xcode_token(SPD)
    headers, request = client.sent[0]
    assert headers["Connection"] == "close"
    assert request["o"] == "apptokens" and request["app"] == [APP]


def test_apptokens_retried_on_503_until_it_works(client):
    client.replies = [Resp(503), Resp(502), token_reply("T")]
    assert client.fetch_xcode_token(SPD) == "T"
    assert len(client.sent) == 3 and client.waits == [2, 4]
    assert all(h["Connection"] == "close" for h, _ in client.sent)


def test_apptokens_gives_up_with_a_clear_error(client):
    client.replies = [Resp(503)] * gsa.APPTOKENS_ATTEMPTS
    with pytest.raises(GsaError, match="token di firma") as info:
        client.fetch_xcode_token(SPD)
    assert info.value.http_status == 503 and len(client.sent) == gsa.APPTOKENS_ATTEMPTS
    fe = friendly_error(info.value)
    assert fe.title == "Apple non ha rilasciato il token di firma"
    assert "Riprova" in fe.hint


def test_other_errors_are_not_retried(client):
    client.replies = [Resp(401)]
    with pytest.raises(GsaError) as info:
        client.fetch_xcode_token(SPD)
    assert info.value.http_status == 401 and len(client.sent) == 1 and client.waits == []


def test_login_steps_are_never_retried(client):
    client.replies = [Resp(503)]
    with pytest.raises(GsaError, match="503") as info:
        client._gsa_request({"o": "init"})
    assert len(client.sent) == 1
    assert friendly_error(info.value).title == "Apple ha rifiutato l'accesso"
