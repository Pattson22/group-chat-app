from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OtpRequest


async def check_and_record_otp_request(db: AsyncSession, phone_number: str, ip_address: str) -> bool:
    """Returns True (and records the attempt) if phone_number/ip_address are
    still under their request limits within the rolling window; False if
    either limit is already hit, in which case nothing is recorded."""
    window_start = datetime.now(timezone.utc) - timedelta(seconds=settings.otp_request_window_seconds)

    phone_count = await db.scalar(
        select(func.count())
        .select_from(OtpRequest)
        .where(OtpRequest.phone_number == phone_number, OtpRequest.requested_at >= window_start)
    )
    ip_count = await db.scalar(
        select(func.count())
        .select_from(OtpRequest)
        .where(OtpRequest.ip_address == ip_address, OtpRequest.requested_at >= window_start)
    )

    if phone_count >= settings.otp_request_limit_per_phone or ip_count >= settings.otp_request_limit_per_ip:
        return False

    db.add(OtpRequest(phone_number=phone_number, ip_address=ip_address))
    await db.commit()
    return True
