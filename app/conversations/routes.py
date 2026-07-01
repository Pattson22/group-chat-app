import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.config import settings
from app.conversations.deps import get_conversation_for_member, require_admin
from app.db import get_db
from app.media.policy import ALLOWED_CONTENT_TYPES
from app.media.storage import storage
from app.models import Conversation, ConversationMember, Media, User
from app.schemas import AddMemberIn, ConversationMemberOut, ConversationOut, CreateDmIn, CreateGroupIn

AVATAR_CONTENT_TYPES = {ct for ct, category in ALLOWED_CONTENT_TYPES.items() if category == "image"}

router = APIRouter(prefix="/conversations", tags=["conversations"])


def make_dm_key(user_a: uuid.UUID, user_b: uuid.UUID) -> str:
    return ":".join(sorted([str(user_a), str(user_b)]))


async def _serialize_conversation(db: AsyncSession, conversation: Conversation) -> ConversationOut:
    result = await db.execute(
        select(ConversationMember, User)
        .join(User, User.id == ConversationMember.user_id)
        .where(ConversationMember.conversation_id == conversation.id)
    )
    members = [
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
    return ConversationOut(
        id=conversation.id,
        type=conversation.type,
        name=conversation.name,
        avatar_media_id=conversation.avatar_media_id,
        created_by=conversation.created_by,
        created_at=conversation.created_at,
        last_message_at=conversation.last_message_at,
        members=members,
    )


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

    result = await db.execute(select(Conversation).where(Conversation.dm_key == dm_key))
    conversation = result.scalar_one_or_none()
    if conversation is None:
        conversation = Conversation(type="dm", dm_key=dm_key, created_by=current_user.id)
        db.add(conversation)
        await db.flush()
        db.add_all(
            [
                ConversationMember(conversation_id=conversation.id, user_id=current_user.id, role="member"),
                ConversationMember(conversation_id=conversation.id, user_id=other_user.id, role="member"),
            ]
        )
        await db.commit()

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
    return [await _serialize_conversation(db, c) for c in conversations]


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
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported content type: {file.content_type}"
        )

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
