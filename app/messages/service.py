import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message


async def create_message(
    db: AsyncSession,
    conversation: Conversation,
    sender_id: uuid.UUID,
    body: str | None = None,
    type: str = "text",
    media_id: uuid.UUID | None = None,
    call_outcome: str | None = None,
    call_video: bool | None = None,
    call_duration_seconds: int | None = None,
) -> Message:
    """Persists a message and bumps the conversation's inbox sort key.

    Shared by the REST send endpoint, the realtime websocket handler for
    text/media messages, and CallManager for call-outcome log entries.
    """
    now = datetime.now(timezone.utc)
    message = Message(
        conversation_id=conversation.id,
        sender_id=sender_id,
        type=type,
        body=body,
        media_id=media_id,
        call_outcome=call_outcome,
        call_video=call_video,
        call_duration_seconds=call_duration_seconds,
        created_at=now,
    )
    db.add(message)
    conversation.last_message_at = now
    await db.commit()
    await db.refresh(message)
    return message
