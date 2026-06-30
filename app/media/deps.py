import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db import get_db
from app.models import ConversationMember, Media, Message, User


async def get_media_for_user(
    media_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Media:
    """Authorizes access to a media item: the uploader can always fetch
    their own upload (e.g. to preview before sending it anywhere); anyone
    else needs to be a member of a conversation containing a message that
    references it. 404 either way if neither holds, so existence isn't
    leaked to outsiders."""
    media = await db.get(Media, media_id)
    if media is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    if media.uploader_id == current_user.id:
        return media

    result = await db.execute(select(Message.conversation_id).where(Message.media_id == media.id))
    conversation_ids = {row[0] for row in result.all()}
    if not conversation_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    member_row = await db.execute(
        select(ConversationMember.conversation_id).where(
            ConversationMember.user_id == current_user.id,
            ConversationMember.conversation_id.in_(conversation_ids),
        )
    )
    if member_row.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    return media
