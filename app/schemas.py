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
    avatar_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


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
    role: str
    joined_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationOut(BaseModel):
    id: uuid.UUID
    type: str
    name: str | None = None
    avatar_url: str | None = None
    created_by: uuid.UUID
    created_at: datetime
    last_message_at: datetime | None = None
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
    created_at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MessagePage(BaseModel):
    items: list[MessageOut]
    has_more: bool
    next_cursor: int | None = None
