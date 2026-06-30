import jwt
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import decode_access_token
from app.db import get_db
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        user_id = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_user_ws(websocket: WebSocket, db: AsyncSession) -> User | None:
    """Authenticate a websocket connection via a `token` query param.

    Not wired into any route yet -- the realtime layer adopts this in
    Phase 3. Returns None if the token is missing/invalid/unrecognized;
    callers should close the connection (e.g. with code 4401) in that case.
    """
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        user_id = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    return await db.get(User, user_id)
