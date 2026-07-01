import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db import get_db
from app.models import Conversation, ConversationMember, Media, Message, User


async def get_media_for_user(
    media_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Media:
    """Authorizes access to a media item: the uploader can always fetch
    their own upload (e.g. to preview before sending it anywhere); anyone
    else needs to be a member of a conversation containing a message that
    references it, *or* the media needs to currently be someone's profile
    avatar (avatars are shown throughout the UI -- inbox list, chat header,
    call tiles -- without a shared conversation message to authorize
    against, so they're treated as visible to any authenticated user,
    unlike message attachments). 404 either way if none of these hold, so
    existence isn't leaked to outsiders."""
    media = await db.get(Media, media_id)
    if media is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    if media.uploader_id == current_user.id:
        return media

    is_avatar = await db.scalar(select(User.id).where(User.avatar_media_id == media.id))
    if is_avatar is not None:
        return media

    # Group avatars are narrower than user avatars: only the group's own
    # members see them (they render in members' inboxes/chat headers, and
    # nowhere else), so this reuses the same membership gate as message
    # attachments rather than the any-authenticated-user rule above.
    result = await db.execute(select(Conversation.id).where(Conversation.avatar_media_id == media.id))
    conversation_ids = {row[0] for row in result.all()}

    result = await db.execute(select(Message.conversation_id).where(Message.media_id == media.id))
    conversation_ids.update(row[0] for row in result.all())
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
