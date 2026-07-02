import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.config import settings
from app.conversations.deps import get_conversation_for_member, require_admin
from app.db import get_db
from app.media.policy import ALLOWED_CONTENT_TYPES
from app.media.storage import storage
from app.models import Conversation, ConversationMember, Media, Message, User
from app.schemas import (
    AddMemberIn,
    ConversationMemberOut,
    ConversationOut,
    CreateDmIn,
    CreateGroupIn,
    MessagePreviewOut,
)

AVATAR_CONTENT_TYPES = {ct for ct, category in ALLOWED_CONTENT_TYPES.items() if category == "image"}

router = APIRouter(prefix="/conversations", tags=["conversations"])


def make_dm_key(user_a: uuid.UUID, user_b: uuid.UUID) -> str:
    return ":".join(sorted([str(user_a), str(user_b)]))


async def _get_dm_by_key(db: AsyncSession, dm_key: str) -> Conversation | None:
    result = await db.execute(select(Conversation).where(Conversation.dm_key == dm_key))
    return result.scalar_one_or_none()


async def _serialize_conversations(db: AsyncSession, conversations: Sequence[Conversation]) -> list[ConversationOut]:
    """Serializes with a fixed query count: members and last-message
    previews are each fetched in one batch across all conversations, so
    the list endpoint doesn't grow a query per conversation."""
    if not conversations:
        return []
    conversation_ids = [c.id for c in conversations]

    result = await db.execute(
        select(ConversationMember, User)
        .join(User, User.id == ConversationMember.user_id)
        .where(ConversationMember.conversation_id.in_(conversation_ids))
    )
    members_by_conversation: dict[uuid.UUID, list[ConversationMemberOut]] = {cid: [] for cid in conversation_ids}
    for member, user in result.all():
        members_by_conversation[member.conversation_id].append(
            ConversationMemberOut(
                user_id=member.user_id,
                phone_number=user.phone_number,
                display_name=user.display_name,
                avatar_media_id=user.avatar_media_id,
                role=member.role,
                joined_at=member.joined_at,
            )
        )

    latest_message_ids = (
        select(func.max(Message.id))
        .where(Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
    )
    result = await db.execute(select(Message).where(Message.id.in_(latest_message_ids)))
    last_message_by_conversation = {m.conversation_id: m for m in result.scalars().all()}

    return [
        ConversationOut(
            id=c.id,
            type=c.type,
            name=c.name,
            avatar_media_id=c.avatar_media_id,
            created_by=c.created_by,
            created_at=c.created_at,
            last_message_at=c.last_message_at,
            last_message=(
                MessagePreviewOut.model_validate(last_message_by_conversation[c.id])
                if c.id in last_message_by_conversation
                else None
            ),
            members=members_by_conversation[c.id],
        )
        for c in conversations
    ]


async def _serialize_conversation(db: AsyncSession, conversation: Conversation) -> ConversationOut:
    return (await _serialize_conversations(db, [conversation]))[0]


@router.post("/dm", response_model=ConversationOut)
async def create_or_get_dm(
    payload: CreateDmIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.other_user_id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot create a DM with yourself")

    other_user = await db.get(User, payload.other_user_id)
    if other_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    dm_key = make_dm_key(current_user.id, other_user.id)

    conversation = await _get_dm_by_key(db, dm_key)
    if conversation is None:
        conversation = Conversation(type="dm", dm_key=dm_key, created_by=current_user.id)
        db.add(conversation)
        try:
            await db.flush()
            db.add_all(
                [
                    ConversationMember(conversation_id=conversation.id, user_id=current_user.id, role="member"),
                    ConversationMember(conversation_id=conversation.id, user_id=other_user.id, role="member"),
                ]
            )
            await db.commit()
        except IntegrityError:
            # A concurrent request beat me to inserting this dm_key; use its row.
            await db.rollback()
            conversation = await _get_dm_by_key(db, dm_key)
            if conversation is None:
                raise

    return await _serialize_conversation(db, conversation)


@router.post("/group", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: CreateGroupIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member_ids = {uid for uid in payload.member_ids if uid != current_user.id}

    if member_ids:
        result = await db.execute(select(User.id).where(User.id.in_(member_ids)))
        found_ids = {row[0] for row in result.all()}
        missing = member_ids - found_ids
        if missing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"User(s) not found: {', '.join(str(m) for m in missing)}")

    conversation = Conversation(type="group", name=payload.name, created_by=current_user.id)
    db.add(conversation)
    await db.flush()

    db.add(ConversationMember(conversation_id=conversation.id, user_id=current_user.id, role="admin"))
    for uid in member_ids:
        db.add(ConversationMember(conversation_id=conversation.id, user_id=uid, role="member"))
    await db.commit()

    return await _serialize_conversation(db, conversation)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(ConversationMember.user_id == current_user.id)
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
    )
    conversations = result.scalars().all()
    return await _serialize_conversations(db, conversations)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation: Conversation = Depends(get_conversation_for_member),
    db: AsyncSession = Depends(get_db),
):
    return await _serialize_conversation(db, conversation)


@router.get("/{conversation_id}/members", response_model=list[ConversationMemberOut])
async def list_members(
    conversation: Conversation = Depends(get_conversation_for_member),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ConversationMember, User)
        .join(User, User.id == ConversationMember.user_id)
        .where(ConversationMember.conversation_id == conversation.id)
    )
    return [
        ConversationMemberOut(
            user_id=member.user_id,
            phone_number=user.phone_number,
            display_name=user.display_name,
            avatar_media_id=user.avatar_media_id,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, user in result.all()
    ]


@router.post("/{conversation_id}/members", response_model=ConversationOut)
async def add_member(
    payload: AddMemberIn,
    conversation: Conversation = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if conversation.type != "group":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot add members to a DM")

    user = await db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    existing = await db.scalar(
        select(ConversationMember).where(
            ConversationMember.conversation_id == conversation.id,
            ConversationMember.user_id == user.id,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is already a member")

    db.add(ConversationMember(conversation_id=conversation.id, user_id=user.id, role="member"))
    await db.commit()

    return await _serialize_conversation(db, conversation)


@router.post("/{conversation_id}/avatar", response_model=ConversationOut)
async def upload_group_avatar(
    file: UploadFile = File(...),
    conversation: Conversation = Depends(require_admin),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # require_admin passes DMs through (they have no admin concept), so the
    # group-only rule needs its own check here.
    if conversation.type != "group":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "DMs cannot have their own avatar")

    if file.content_type not in AVATAR_CONTENT_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported content type: {file.content_type}")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(data) > settings.media_max_size_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File too large (max {settings.media_max_size_bytes} bytes)",
        )

    storage_key = await storage.save(data, file.content_type)
    media = Media(
        uploader_id=current_user.id,
        storage_backend=settings.storage_backend,
        storage_key=storage_key,
        content_type=file.content_type,
        size_bytes=len(data),
    )
    db.add(media)
    await db.flush()

    conversation.avatar_media_id = media.id
    await db.commit()

    return await _serialize_conversation(db, conversation)


@router.delete("/{conversation_id}/avatar", response_model=ConversationOut)
async def delete_group_avatar(
    conversation: Conversation = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if conversation.type != "group":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "DMs cannot have their own avatar")

    conversation.avatar_media_id = None
    await db.commit()

    return await _serialize_conversation(db, conversation)
