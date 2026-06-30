import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message


async def create_message(
    db: AsyncSession,
    conversation: Conversation,
    sender_id: uuid.UUID,
    body: str,
    type: str = "text",
) -> Message:
    """Persists a message and bumps the conversation's inbox sort key.

    Shared by the REST send endpoint now and the realtime websocket
    handler once Phase 3 wires live delivery on top of this.
    """
    now = datetime.now(timezone.utc)
    message = Message(conversation_id=conversation.id, sender_id=sender_id, type=type, body=body, created_at=now)
    db.add(message)
    conversation.last_message_at = now
    await db.commit()
    await db.refresh(message)
    return message
