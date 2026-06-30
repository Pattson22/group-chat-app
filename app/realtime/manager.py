import uuid
from typing import Dict, List

from fastapi import WebSocket


class ConnectionManager:
    """Tracks a user's live websocket connections (a user may have more
    than one -- multiple tabs/devices)."""

    def __init__(self):
        self.active_connections: Dict[uuid.UUID, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: uuid.UUID):
        await websocket.accept()
        self.active_connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: uuid.UUID):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def send_to_user(self, message: str, user_id: uuid.UUID):
        """Sends to every active connection for a single user, pruning any
        that fail (stale/closed connections) instead of letting one bad
        connection break delivery to the rest."""
        if user_id not in self.active_connections:
            return
        dead_connections = []
        for connection in self.active_connections[user_id]:
            try:
                await connection.send_text(message)
            except Exception:
                dead_connections.append(connection)
        for connection in dead_connections:
            self.disconnect(connection, user_id)

    async def broadcast_to_users(self, message: str, user_ids: List[uuid.UUID]):
        for user_id in user_ids:
            await self.send_to_user(message, user_id)
