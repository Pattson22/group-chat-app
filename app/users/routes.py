from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db import get_db
from app.models import User
from app.phone import normalize_phone_number
from app.schemas import UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/lookup", response_model=UserOut)
async def lookup_user(
    phone_number: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Finds a user by phone number to start a DM with. There's no
    contacts/address-book concept yet -- this is the minimal lookup the
    "new chat by phone number" flow needs."""
    normalized = normalize_phone_number(phone_number)
    if normalized is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Phone number must be a valid number in E.164 format, e.g. +15551234567"
        )

    result = await db.execute(select(User).where(User.phone_number == normalized))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No user found with that phone number")

    return UserOut.model_validate(user)
