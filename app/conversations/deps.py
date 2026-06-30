import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db import get_db
from app.models import Conversation, ConversationMember, User


async def get_conversation_for_user_id(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    """Non-HTTP membership lookup -- returns None rather than raising, for
    callers like the websocket handler that aren't in a request/response
    cycle and need to decide for themselves how to react."""
    result = await db.execute(
        select(Conversation)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(Conversation.id == conversation_id, ConversationMember.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_member_user_ids(db: AsyncSession, conversation_id: uuid.UUID) -> list[uuid.UUID]:
    result = await db.execute(
        select(ConversationMember.user_id).where(ConversationMember.conversation_id == conversation_id)
    )
    return [row[0] for row in result.all()]


async def get_conversation_for_member(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    """Loads a conversation, scoped to membership.

    Returns 404 -- not 403 -- when the conversation exists but the caller
    isn't a member, so a non-member can't distinguish "doesn't exist" from
    "exists but you're not in it."
    """
    conversation = await get_conversation_for_user_id(db, conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


async def require_admin(
    conversation: Conversation = Depends(get_conversation_for_member),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    """Use after get_conversation_for_member to additionally require the
    caller be a group admin. DMs have no admin concept, so they pass through."""
    if conversation.type != "group":
        return conversation

    role = await db.scalar(
        select(ConversationMember.role).where(
            ConversationMember.conversation_id == conversation.id,
            ConversationMember.user_id == current_user.id,
        )
    )
    if role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only group admins can do this")
    return conversation
