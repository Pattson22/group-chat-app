import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.otp import otp_provider
from app.auth.rate_limit import check_and_record_otp_request
from app.auth.tokens import create_access_token, generate_refresh_token, hash_refresh_token
from app.db import get_db
from app.models import RefreshToken, User
from app.schemas import RefreshIn, RequestOtpIn, RequestOtpOut, TokenOut, UserOut, VerifyOtpIn

router = APIRouter(prefix="/auth", tags=["auth"])

# Basic E.164 sanity check (+ country code + up to 15 digits total).
PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")


@router.post("/request-otp", response_model=RequestOtpOut)
async def request_otp(payload: RequestOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    if not PHONE_RE.match(payload.phone_number):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Phone number must be in E.164 format, e.g. +15551234567")

    client_ip = request.client.host if request.client else "unknown"
    allowed = await check_and_record_otp_request(db, payload.phone_number, client_ip)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many OTP requests, try again later")

    dev_code = await otp_provider.start_verification(payload.phone_number)
    return RequestOtpOut(dev_otp_code=dev_code)


@router.post("/verify-otp", response_model=TokenOut)
async def verify_otp(payload: VerifyOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    ok = await otp_provider.check_verification(payload.phone_number, payload.code)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")

    result = await db.execute(select(User).where(User.phone_number == payload.phone_number))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(phone_number=payload.phone_number)
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
