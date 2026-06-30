import json
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_ws
from app.auth.routes import router as auth_router
from app.config import settings
from app.conversations.deps import get_conversation_for_user_id, get_member_user_ids
from app.conversations.routes import router as conversations_router
from app.db import get_db
from app.media.policy import ALLOWED_CONTENT_TYPES
from app.media.routes import router as media_router
from app.messages.routes import router as messages_router
from app.messages.service import create_message
from app.models import Media
from app.realtime.manager import ConnectionManager
from app.schemas import MessageOut

app = FastAPI()
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(messages_router)
app.include_router(media_router)

FRONTEND_DIR = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


manager = ConnectionManager()


# --- ROUTES ---
@app.get("/")
async def get():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    user = await get_current_user_ws(websocket, db)
    if user is None:
        # Closing before accept() would surface as a generic HTTP 403 at
        # the handshake instead of a real WS close frame, so accept first
        # and immediately close with a distinguishable app-level code.
        await websocket.accept()
        await websocket.close(code=4401)
        return

    await manager.connect(websocket, user.id)
    message_times = deque()

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                payload = json.loads(raw)
                conversation_id = uuid.UUID(payload["conversation_id"])
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, AttributeError):
                await manager.send_personal_message(
                    json.dumps({"type": "system", "text": "Malformed message"}), websocket
                )
                continue

            # A message is either text ({"text": "..."}) or a reference to
            # a file/image the sender already uploaded via POST /media/upload
            # ({"media_id": "..."}).
            media_id_raw = payload.get("media_id")
            text_body = payload.get("text")

            if media_id_raw is not None:
                try:
                    media_id = uuid.UUID(media_id_raw)
                except (ValueError, TypeError, AttributeError):
                    await manager.send_personal_message(
                        json.dumps({"type": "system", "text": "Malformed message"}), websocket
                    )
                    continue

                media = await db.get(Media, media_id)
                if media is None or media.uploader_id != user.id:
                    await manager.send_personal_message(
                        json.dumps({"type": "system", "text": "Invalid media reference"}), websocket
                    )
                    continue

                msg_type = ALLOWED_CONTENT_TYPES.get(media.content_type, "file")
                msg_body = None
                msg_media_id = media.id
            elif isinstance(text_body, str) and text_body.strip():
                msg_type = "text"
                msg_body = text_body
                msg_media_id = None
            else:
                await manager.send_personal_message(
                    json.dumps({"type": "system", "text": "Message must include non-empty text or a media_id"}),
                    websocket,
                )
                continue

            # Rate limit: drop messages once a client exceeds
            # rate_limit_messages within rate_limit_window_seconds.
            send_time = time.monotonic()
            while message_times and send_time - message_times[0] > settings.rate_limit_window_seconds:
                message_times.popleft()
            if len(message_times) >= settings.rate_limit_messages:
                warning = json.dumps({"type": "system", "text": "You're sending messages too fast. Please slow down."})
                await manager.send_personal_message(warning, websocket)
                continue
            message_times.append(send_time)

            # Authorization boundary: only conversation members may post
            # to (or receive broadcasts targeting) a conversation_id.
            conversation = await get_conversation_for_user_id(db, conversation_id, user.id)
            if conversation is None:
                await manager.send_personal_message(
                    json.dumps({"type": "system", "text": "You are not a member of that conversation"}), websocket
                )
                continue

            message = await create_message(db, conversation, user.id, body=msg_body, type=msg_type, media_id=msg_media_id)
            member_ids = await get_member_user_ids(db, conversation.id)

            event = {"type": "message", "message": MessageOut.model_validate(message).model_dump(mode="json")}
            await manager.broadcast_to_users(json.dumps(event), member_ids)

    except WebSocketDisconnect:
        manager.disconnect(websocket, user.id)
