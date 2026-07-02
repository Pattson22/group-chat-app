import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.config import settings
from app.conversations.deps import get_conversation_for_member, get_member_user_ids
from app.db import get_db
from app.messages.service import create_message
from app.models import Conversation, Message, User
from app.realtime.manager import manager
from app.schemas import MessageOut, MessagePage, SendMessageIn

router = APIRouter(prefix="/conversations", tags=["messages"])


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    payload: SendMessageIn,
    conversation: Conversation = Depends(get_conversation_for_member),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not payload.body.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message body cannot be empty")
    if len(payload.body) > settings.message_max_length:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Message too long (max {settings.message_max_length} characters)"
        )

    message = await create_message(db, conversation, current_user.id, payload.body)
    message_out = MessageOut.model_validate(message)

    # Same event the websocket path broadcasts, so members who are online
    # see REST-sent messages live instead of on next page load.
    event = {"type": "message", "message": message_out.model_dump(mode="json")}
    await manager.broadcast_to_users(json.dumps(event), await get_member_user_ids(db, conversation.id))

    return message_out


@router.get("/{conversation_id}/messages", response_model=MessagePage)
async def get_messages(
    conversation: Conversation = Depends(get_conversation_for_member),
    db: AsyncSession = Depends(get_db),
    before: int | None = Query(default=None, description="Return messages with id < before"),
    limit: int = Query(default=50, ge=1, le=200),
):
    query = select(Message).where(Message.conversation_id == conversation.id)
    if before is not None:
        query = query.where(Message.id < before)
    query = query.order_by(Message.id.desc()).limit(limit + 1)

    result = await db.execute(query)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # oldest-first within the page, for natural display order

    next_cursor = rows[0].id if has_more and rows else None

    return MessagePage(
        items=[MessageOut.model_validate(m) for m in rows],
        has_more=has_more,
        next_cursor=next_cursor,
    )
