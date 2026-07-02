from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.otp import otp_provider
from app.auth.rate_limit import (
    check_and_record_otp_request,
    has_otp_verify_attempts_remaining,
    record_otp_verify_failure,
)
from app.auth.tokens import create_access_token, generate_refresh_token, hash_refresh_token
from app.config import settings
from app.db import get_db
from app.media.policy import ALLOWED_CONTENT_TYPES
from app.media.storage import storage
from app.models import Media, RefreshToken, User
from app.phone import normalize_phone_number
from app.schemas import RefreshIn, RequestOtpIn, RequestOtpOut, TokenOut, UpdateMeIn, UserOut, VerifyOtpIn

router = APIRouter(prefix="/auth", tags=["auth"])

PHONE_FORMAT_ERROR = "Phone number must be a valid number in E.164 format, e.g. +15551234567"
AVATAR_CONTENT_TYPES = {ct for ct, category in ALLOWED_CONTENT_TYPES.items() if category == "image"}


@router.post("/request-otp", response_model=RequestOtpOut)
async def request_otp(payload: RequestOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    phone_number = normalize_phone_number(payload.phone_number)
    if phone_number is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, PHONE_FORMAT_ERROR)

    client_ip = request.client.host if request.client else "unknown"
    allowed = await check_and_record_otp_request(db, phone_number, client_ip)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many OTP requests, try again later")

    dev_code = await otp_provider.start_verification(phone_number)
    return RequestOtpOut(dev_otp_code=dev_code)


@router.post("/verify-otp", response_model=TokenOut)
async def verify_otp(payload: VerifyOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    phone_number = normalize_phone_number(payload.phone_number)
    if phone_number is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, PHONE_FORMAT_ERROR)

    client_ip = request.client.host if request.client else "unknown"

    if not await has_otp_verify_attempts_remaining(db, phone_number):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts, request a new code")

    ok = await otp_provider.check_verification(phone_number, payload.code)
    if not ok:
        await record_otp_verify_failure(db, phone_number, client_ip)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")

    result = await db.execute(select(User).where(User.phone_number == phone_number))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(phone_number=phone_number)
        db.add(user)
        await db.flush()

    access_token = create_access_token(user.id)
    raw_refresh, refresh_hash, expires_at = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=expires_at,
            user_agent=request.headers.get("user-agent"),
        )
    )
    await db.commit()

    return TokenOut(access_token=access_token, refresh_token=raw_refresh, user=UserOut.model_validate(user))


@router.post("/refresh", response_model=TokenOut)
async def refresh(payload: RefreshIn, request: Request, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(payload.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is None or stored.revoked_at is not None or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    # Rotate: revoke the used token and issue a fresh pair.
    stored.revoked_at = datetime.now(timezone.utc)
    user = await db.get(User, stored.user_id)

    access_token = create_access_token(user.id)
    raw_refresh, refresh_hash, expires_at = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=expires_at,
            user_agent=request.headers.get("user-agent"),
        )
    )
    await db.commit()

    return TokenOut(access_token=access_token, refresh_token=raw_refresh, user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(payload.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(timezone.utc)
        await db.commit()


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UpdateMeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.display_name is not None:
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Display name cannot be empty")
        current_user.display_name = display_name

    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)


@router.post("/me/avatar", response_model=UserOut)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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

    # The previous avatar's Media row (if any) is left in place rather than
    # deleted -- same "no cleanup job" tradeoff as unreferenced message
    # uploads elsewhere in this app.
    current_user.avatar_media_id = media.id
    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)


@router.delete("/me/avatar", response_model=UserOut)
async def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.avatar_media_id = None
    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)
