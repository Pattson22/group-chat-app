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
) -> Message:
    """Persists a message and bumps the conversation's inbox sort key.

    Shared by the REST send endpoint, and the realtime websocket handler
    for both text and media (image/file) messages.
    """
    now = datetime.now(timezone.utc)
    message = Message(
        conversation_id=conversation.id,
        sender_id=sender_id,
        type=type,
        body=body,
        media_id=media_id,
        created_at=now,
    )
    db.add(message)
    conversation.last_message_at = now
    await db.commit()
    await db.refresh(message)
    return message
