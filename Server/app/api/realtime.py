"""One-use, resource-scoped WebSocket tickets. Redis failure denies access."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user
from app.core.encryption import token_encryptor
from app.core.security import decode_token
from app.database.models import User
from app.database.session import get_db
from app.services.note_access import ensure_note_permission
from app.services.thinking_sessions import ensure_thinking_session_permission, get_thinking_session_or_404

router = APIRouter(prefix="/realtime", tags=["Realtime"])
TICKET_TTL_SECONDS = 60


class TicketRequest(BaseModel):
    room: str = Field(pattern=r"^(note|thinking-session):[0-9a-fA-F-]{36}$")


    @field_validator("room")
    @classmethod
    def canonical_room(cls, value: str) -> str:
        return value.lower()


class RedeemRequest(TicketRequest):
    ticket: str = Field(min_length=40, max_length=128)


def ticket_key(ticket: str) -> str:
    return "anfinity:realtime:ticket:" + hashlib.sha256(ticket.encode()).hexdigest()


async def check_access(room: str, user: User, db: AsyncSession) -> None:
    kind, resource_id = room.split(":", 1)
    try:
        resource_uuid = UUID(resource_id)
    except ValueError:
        raise HTTPException(422, "Invalid resource") from None
    if kind == "note":
        from app.api.notes import load_note_or_404
        note = await load_note_or_404(resource_uuid, db)
        await ensure_note_permission(note, user, db, "view")
    else:
        session = await get_thinking_session_or_404(resource_uuid, db)
        await ensure_thinking_session_permission(session, user, db, "view")


@router.post("/ticket")
async def issue_ticket(payload: TicketRequest, request: Request,
                       user: User = Depends(get_current_active_user),
                       db: AsyncSession = Depends(get_db)):
    await check_access(payload.room, user, db)
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer authentication required")
    token = authorization[7:].strip()
    ticket = secrets.token_urlsafe(32)
    record = json.dumps({"room": payload.room, "token": token_encryptor.encrypt(token), "aud": "anfinity-realtime"})
    try:
        async with Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3) as redis:
            await redis.set(ticket_key(ticket), record, ex=TICKET_TTL_SECONDS, nx=True)
    except Exception:
        raise HTTPException(503, "Realtime tickets temporarily unavailable") from None
    # The ticket is returned in a POST body and must never be cached.
    from fastapi.responses import JSONResponse
    return JSONResponse({"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}, headers={"Cache-Control": "no-store"})


@router.post("/redeem")
async def redeem_ticket(payload: RedeemRequest, request: Request):
    expected = settings.REALTIME_SERVICE_SECRET
    offered = request.headers.get("x-realtime-secret", "")
    if not expected or not hmac.compare_digest(expected.encode(), offered.encode()):
        raise HTTPException(401, "Unauthorized")
    try:
        async with Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3) as redis:
            # Atomic consumption across all API replicas; Redis >= 6.2.
            raw = await redis.getdel(ticket_key(payload.ticket))
    except Exception:
        raise HTTPException(503, "Realtime tickets temporarily unavailable") from None
    if not raw:
        raise HTTPException(401, "Expired or consumed ticket")
    try:
        record = json.loads(raw)
        if record.get("room") != payload.room or record.get("aud") != "anfinity-realtime":
            raise ValueError("Invalid ticket scope")
        token = token_encryptor.decrypt(record["token"])
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(401, "Invalid ticket") from None
    if not decode_token(token):
        raise HTTPException(401, "Expired credentials")
    # Worker checks current identity and permissions before upgrading.
    from fastapi.responses import JSONResponse
    return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})
