import uuid

from pydantic import BaseModel, ConfigDict


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
