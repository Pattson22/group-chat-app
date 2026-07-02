import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RequestOtpIn(BaseModel):
    phone_number: str


class RequestOtpOut(BaseModel):
    # Only populated when settings.otp_provider == "dev", so local/test
    # flows don't need a real SMS provider to complete the loop.
    dev_otp_code: str | None = None


class VerifyOtpIn(BaseModel):
    phone_number: str
    code: str


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    phone_number: str
    display_name: str | None = None
    avatar_media_id: uuid.UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class UpdateMeIn(BaseModel):
    display_name: str | None = None


class CreateDmIn(BaseModel):
    other_user_id: uuid.UUID


class CreateGroupIn(BaseModel):
    name: str
    member_ids: list[uuid.UUID] = Field(default_factory=list)


class AddMemberIn(BaseModel):
    user_id: uuid.UUID


class ConversationMemberOut(BaseModel):
    user_id: uuid.UUID
    phone_number: str
    display_name: str | None = None
    avatar_media_id: uuid.UUID | None = None
    role: str
    joined_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessagePreviewOut(BaseModel):
    """Slim view of a conversation's newest message, just enough for the
    sidebar preview line ("You: hey", "Photo", "Missed call"...)."""

    sender_id: uuid.UUID
    type: str
    body: str | None = None
    call_outcome: str | None = None
    call_video: bool | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationOut(BaseModel):
    id: uuid.UUID
    type: str
    name: str | None = None
    avatar_media_id: uuid.UUID | None = None
    created_by: uuid.UUID
    created_at: datetime
    last_message_at: datetime | None = None
    last_message: MessagePreviewOut | None = None
    members: list[ConversationMemberOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class SendMessageIn(BaseModel):
    body: str


class MessageOut(BaseModel):
    id: int
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    type: str
    body: str | None = None
    media_id: uuid.UUID | None = None
    call_outcome: str | None = None
    call_video: bool | None = None
    call_duration_seconds: int | None = None
    created_at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MessagePage(BaseModel):
    items: list[MessageOut]
    has_more: bool
    next_cursor: int | None = None


class MediaOut(BaseModel):
    id: uuid.UUID
    content_type: str
    size_bytes: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
