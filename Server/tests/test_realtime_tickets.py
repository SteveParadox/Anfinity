from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest
from fastapi import HTTPException
from app.api import realtime

ROOM = "note:11111111-1111-4111-8111-111111111111"


class RedisStub:
    def __init__(self):
        self.records = {}
        self.ttl = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def set(self, key, value, *, ex, nx):
        self.ttl = ex
        assert nx
        self.records[key] = value

    async def getdel(self, key):
        return self.records.pop(key, None)


@pytest.fixture
def ticket_store(monkeypatch):
    store = RedisStub()
    monkeypatch.setattr(realtime.Redis, "from_url", lambda *_: store)
    monkeypatch.setattr(realtime, "check_access", AsyncMock())
    monkeypatch.setattr(realtime, "decode_token", lambda _: {"sub": "user"})
    monkeypatch.setattr(realtime.settings, "REALTIME_SERVICE_SECRET", "test-worker-secret")
    return store


async def issue():
    result = await realtime.issue_ticket(realtime.TicketRequest(room=ROOM),
        SimpleNamespace(headers={"authorization": "Bearer private-bearer"}), SimpleNamespace(id="user"), object())
    assert result.headers["cache-control"] == "no-store"
    return json.loads(result.body)["ticket"]


def worker_request(secret="test-worker-secret"):
    return SimpleNamespace(headers={"x-realtime-secret": secret})


@pytest.mark.asyncio
async def test_ticket_is_scoped_encrypted_short_lived_and_one_use(ticket_store):
    ticket = await issue()
    assert ticket_store.ttl == 60
    assert "private-bearer" not in next(iter(ticket_store.records.values()))
    payload = realtime.RedeemRequest(room=ROOM, ticket=ticket)
    assert json.loads((await realtime.redeem_ticket(payload, worker_request())).body) == {"token": "private-bearer"}
    with pytest.raises(HTTPException) as error:
        await realtime.redeem_ticket(payload, worker_request())
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_ticket_cannot_be_used_for_another_room(ticket_store):
    ticket = await issue()
    with pytest.raises(HTTPException) as error:
        await realtime.redeem_ticket(realtime.RedeemRequest(room=ROOM.replace("note:", "thinking-session:"), ticket=ticket), worker_request())
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_redemption_requires_worker_secret_and_expired_tickets_fail(ticket_store):
    ticket = await issue()
    payload = realtime.RedeemRequest(room=ROOM, ticket=ticket)
    with pytest.raises(HTTPException) as error:
        await realtime.redeem_ticket(payload, worker_request("wrongé"))
    assert error.value.status_code == 401
    ticket_store.records.clear()
    with pytest.raises(HTTPException) as error:
        await realtime.redeem_ticket(payload, worker_request())
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_issue_denies_revoked_permissions(ticket_store, monkeypatch):
    monkeypatch.setattr(realtime, "check_access", AsyncMock(side_effect=HTTPException(403, "Forbidden")))
    with pytest.raises(HTTPException) as error:
        await issue()
    assert error.value.status_code == 403
    assert not ticket_store.records


@pytest.mark.asyncio
async def test_redis_failure_does_not_fall_back_to_unprotected_rooms(ticket_store, monkeypatch):
    monkeypatch.setattr(ticket_store, "set", AsyncMock(side_effect=ConnectionError()))
    with pytest.raises(HTTPException) as error:
        await issue()
    assert error.value.status_code == 503
