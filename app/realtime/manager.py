import uuid
from typing import Dict, List

from fastapi import WebSocket


class ConnectionManager:
    """Tracks a user's live websocket connections (a user may have more
    than one -- multiple tabs/devices).

    Scaling limitation: active_connections is an in-memory dict on this
    process. A message from a user connected to server instance A will
    never reach a recipient connected to instance B -- this only works
    correctly with a single backend process. That's fine for now (single
    instance), but becomes a real problem the moment this needs to run
    behind a load balancer with multiple instances, or survive zero-
    downtime rolling deploys.

    Upgrade path when that's needed: introduce a pub/sub backplane (Redis
    pub/sub is the standard choice; Postgres LISTEN/NOTIFY is a viable
    lower-effort alternative given Postgres is already the datastore) so
    that publishing a message broadcasts it to *all* instances, each of
    which then fans it out to whichever of the target users happen to be
    connected locally. The call sites in app/main.py (`broadcast_to_users`)
    are written against this class's public interface specifically so that
    swap can happen here without changing the websocket route itself --
    only `send_to_user`/`broadcast_to_users` would need to publish to the
    backplane instead of writing directly to local sockets, and a new
    subscriber loop would call the existing per-connection send logic for
    sockets live on that instance.
    """

    def __init__(self):
        self.active_connections: Dict[uuid.UUID, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: uuid.UUID, subprotocol: str | None = None):
        await websocket.accept(subprotocol=subprotocol)
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


# Process-wide singleton, defined here (not in app.main) so REST endpoints
# that push realtime events can import it without a circular import.
manager = ConnectionManager()
