from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.config import settings
from app.db import get_db
from app.media.deps import get_media_for_user
from app.media.policy import ALLOWED_CONTENT_TYPES
from app.media.storage import storage
from app.models import Media, User
from app.schemas import MediaOut

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload", response_model=MediaOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
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
    await db.commit()
    await db.refresh(media)

    return MediaOut.model_validate(media)


@router.get("/{media_id}")
async def download_media(media: Media = Depends(get_media_for_user)):
    try:
        data = await storage.read(media.storage_key)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    return Response(content=data, media_type=media.content_type)
