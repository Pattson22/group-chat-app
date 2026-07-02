import json
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.conversations.deps import get_conversation_for_user_id, get_member_user_ids
from app.messages.service import create_message
from app.models import Conversation, User
from app.realtime.manager import ConnectionManager
from app.schemas import MessageOut


@dataclass
class CallState:
    call_id: uuid.UUID
    conversation_id: uuid.UUID
    caller_id: uuid.UUID
    video: bool
    participants: set = field(default_factory=set)  # currently joined
    ringing: set = field(default_factory=set)  # invited, awaiting response
    ever_joined: set = field(default_factory=set)  # union over the call's life -> outcome
    was_ever_rung: bool = False  # whether to log anything at all if it never connects
    started_at: float = field(default_factory=time.monotonic)


class CallManager:
    """Ephemeral, in-memory call signaling state -- mirrors
    ConnectionManager's single-process limitation (no Redis/pub-sub yet,
    documented there); calls don't survive a server restart, which is fine
    since a dropped call is just re-dialed.

    Mesh WebRTC only (no SFU): every participant connects directly to every
    other participant, so settings.call_max_participants keeps peer-connection
    count and per-peer upload multiplication bounded.

    Known gap: an unclean websocket disconnect (network drop, not a clean
    close) is only noticed by the *server* the next time it tries to write
    to that socket -- there's no heartbeat/reaper. A dropped *peer media*
    connection is instead detected client-side via
    RTCPeerConnection.connectionState (app/frontend/calls.js), independent
    of whether the server has noticed the websocket is gone yet.
    """

    def __init__(self):
        self.calls: dict[uuid.UUID, CallState] = {}
        self.call_id_by_conversation: dict[uuid.UUID, uuid.UUID] = {}

    def user_active_call(self, user_id: uuid.UUID) -> CallState | None:
        for call in self.calls.values():
            if user_id in call.participants:
                return call
        return None

    async def handle_call_action(
        self, action: str, payload: dict, user: User, db: AsyncSession, manager: ConnectionManager
    ) -> None:
        try:
            if action == "call:invite":
                await self._handle_invite(payload, user, db, manager)
            elif action == "call:accept":
                await self._handle_accept(payload, user, manager)
            elif action == "call:decline":
                await self._handle_decline_action(payload, user, db, manager)
            elif action == "call:leave":
                await self._handle_leave_action(payload, user, db, manager)
            elif action in ("call:offer", "call:answer", "call:ice-candidate"):
                await self._handle_relay(action, payload, user, manager)
            else:
                await self._send_error(manager, user.id, None, "malformed")
        except (KeyError, ValueError, TypeError, AttributeError):
            await self._send_error(manager, user.id, payload.get("call_id"), "malformed")

    async def handle_user_disconnected(self, user_id: uuid.UUID, db: AsyncSession, manager: ConnectionManager) -> None:
        for call in list(self.calls.values()):
            if user_id in call.participants:
                await self._leave_participant(call, user_id, db, manager)
            elif user_id in call.ringing:
                await self._decline_ringing(call, user_id, db, manager)

    # --- invite / join ---

    async def _handle_invite(self, payload: dict, user: User, db: AsyncSession, manager: ConnectionManager) -> None:
        conversation_id = uuid.UUID(payload["conversation_id"])
        video = bool(payload.get("video", False))

        conversation = await get_conversation_for_user_id(db, conversation_id, user.id)
        if conversation is None:
            await self._send_error(manager, user.id, None, "not_a_member")
            return

        existing_call_id = self.call_id_by_conversation.get(conversation_id)

        active = self.user_active_call(user.id)
        if active is not None and active.call_id != existing_call_id:
            await self._send_error(manager, user.id, existing_call_id, "already_in_call")
            return

        if existing_call_id is None:
            await self._start_call(conversation_id, video, user, db, manager)
        else:
            await self._join_call(self.calls[existing_call_id], user, manager)

    async def _start_call(
        self, conversation_id: uuid.UUID, video: bool, user: User, db: AsyncSession, manager: ConnectionManager
    ) -> None:
        call = CallState(
            call_id=uuid.uuid4(),
            conversation_id=conversation_id,
            caller_id=user.id,
            video=video,
            participants={user.id},
            ever_joined={user.id},
        )

        member_ids = await get_member_user_ids(db, conversation_id)
        rung, busy, unreachable = [], [], []
        for member_id in member_ids:
            if member_id == user.id:
                continue
            if member_id not in manager.active_connections:
                unreachable.append(member_id)
            elif self.user_active_call(member_id) is not None:
                busy.append(member_id)
            else:
                call.ringing.add(member_id)
                rung.append(member_id)

        call.was_ever_rung = bool(rung)
        self.calls[call.call_id] = call
        self.call_id_by_conversation[conversation_id] = call.call_id

        for member_id in rung:
            await manager.send_to_user(
                json.dumps(
                    {
                        "type": "call",
                        "action": "call:incoming",
                        "call_id": str(call.call_id),
                        "conversation_id": str(conversation_id),
                        "caller_id": str(user.id),
                        "video": video,
                    }
                ),
                member_id,
            )

        await manager.send_to_user(
            json.dumps(
                {
                    "type": "call",
                    "action": "call:invited",
                    "call_id": str(call.call_id),
                    "conversation_id": str(conversation_id),
                    "video": video,
                    "rung_user_ids": [str(u) for u in rung],
                    "busy_user_ids": [str(u) for u in busy],
                    "unreachable_user_ids": [str(u) for u in unreachable],
                }
            ),
            user.id,
        )

        if not rung:
            # Nobody could be reached -- nothing to wait for, nothing to log.
            self.calls.pop(call.call_id, None)
            self.call_id_by_conversation.pop(conversation_id, None)

    async def _join_call(self, call: CallState, user: User, manager: ConnectionManager) -> None:
        if len(call.participants) >= settings.call_max_participants:
            await self._send_error(manager, user.id, call.call_id, "call_full")
            return

        call.ringing.discard(user.id)
        existing = list(call.participants)
        call.participants.add(user.id)
        call.ever_joined.add(user.id)

        for participant_id in existing:
            await manager.send_to_user(
                json.dumps(
                    {
                        "type": "call",
                        "action": "call:participant-joined",
                        "call_id": str(call.call_id),
                        "user_id": str(user.id),
                    }
                ),
                participant_id,
            )

        await manager.send_to_user(
            json.dumps(
                {
                    "type": "call",
                    "action": "call:joined",
                    "call_id": str(call.call_id),
                    "conversation_id": str(call.conversation_id),
                    "video": call.video,
                    "participants": [str(p) for p in existing],
                }
            ),
            user.id,
        )

    # --- accept / decline / leave ---

    async def _handle_accept(self, payload: dict, user: User, manager: ConnectionManager) -> None:
        call_id = uuid.UUID(payload["call_id"])
        call = self.calls.get(call_id)
        if call is None:
            await self._send_error(manager, user.id, call_id, "call_not_found")
            return
        if user.id not in call.ringing:
            await self._send_error(manager, user.id, call_id, "not_invited")
            return
        await self._join_call(call, user, manager)

    async def _handle_decline_action(
        self, payload: dict, user: User, db: AsyncSession, manager: ConnectionManager
    ) -> None:
        call_id = uuid.UUID(payload["call_id"])
        call = self.calls.get(call_id)
        if call is None:
            await self._send_error(manager, user.id, call_id, "call_not_found")
            return
        if user.id not in call.ringing:
            await self._send_error(manager, user.id, call_id, "not_invited")
            return
        await self._decline_ringing(call, user.id, db, manager)

    async def _decline_ringing(
        self, call: CallState, user_id: uuid.UUID, db: AsyncSession, manager: ConnectionManager
    ) -> None:
        call.ringing.discard(user_id)
        for participant_id in call.participants:
            await manager.send_to_user(
                json.dumps(
                    {"type": "call", "action": "call:declined", "call_id": str(call.call_id), "user_id": str(user_id)}
                ),
                participant_id,
            )

        if not call.ringing and len(call.participants) == 1:
            # Only the caller is left, nobody else ever joined, and nobody
            # is left to ring -- this call will never connect.
            caller_id = call.caller_id
            await manager.send_to_user(
                json.dumps({"type": "call", "action": "call:ended", "call_id": str(call.call_id), "outcome": "missed"}),
                caller_id,
            )
            call.participants.discard(caller_id)
            await self._teardown_and_log(call, db, manager)

    async def _handle_leave_action(
        self, payload: dict, user: User, db: AsyncSession, manager: ConnectionManager
    ) -> None:
        call_id = uuid.UUID(payload["call_id"])
        call = self.calls.get(call_id)
        if call is None:
            return  # already gone -- leaving is idempotent
        if user.id in call.participants:
            await self._leave_participant(call, user.id, db, manager)
        elif user.id in call.ringing:
            await self._decline_ringing(call, user.id, db, manager)

    async def _leave_participant(
        self, call: CallState, user_id: uuid.UUID, db: AsyncSession, manager: ConnectionManager
    ) -> None:
        call.participants.discard(user_id)
        for participant_id in call.participants:
            await manager.send_to_user(
                json.dumps(
                    {
                        "type": "call",
                        "action": "call:participant-left",
                        "call_id": str(call.call_id),
                        "user_id": str(user_id),
                    }
                ),
                participant_id,
            )

        if not call.participants:
            if call.ringing:
                still_ringing = list(call.ringing)
                call.ringing.clear()
                for ringing_id in still_ringing:
                    await manager.send_to_user(
                        json.dumps({"type": "call", "action": "call:cancelled", "call_id": str(call.call_id)}),
                        ringing_id,
                    )
            await self._teardown_and_log(call, db, manager)

    # --- WebRTC negotiation relay ---

    async def _handle_relay(self, action: str, payload: dict, user: User, manager: ConnectionManager) -> None:
        call_id = uuid.UUID(payload["call_id"])
        target_user_id = uuid.UUID(payload["target_user_id"])
        call = self.calls.get(call_id)
        if call is None:
            await self._send_error(manager, user.id, call_id, "call_not_found")
            return
        if user.id not in call.participants:
            await self._send_error(manager, user.id, call_id, "not_a_participant")
            return

        event = {"type": "call", "action": action, "call_id": str(call_id), "from_user_id": str(user.id)}
        if action == "call:ice-candidate":
            event["candidate"] = payload["candidate"]
        else:
            event["sdp"] = payload["sdp"]
        await manager.send_to_user(json.dumps(event), target_user_id)

    # --- teardown / logging ---

    async def _teardown_and_log(self, call: CallState, db: AsyncSession, manager: ConnectionManager) -> None:
        self.calls.pop(call.call_id, None)
        if self.call_id_by_conversation.get(call.conversation_id) == call.call_id:
            del self.call_id_by_conversation[call.conversation_id]

        if len(call.ever_joined) >= 2:
            outcome, duration = "completed", int(time.monotonic() - call.started_at)
        elif call.was_ever_rung:
            outcome, duration = "missed", None
        else:
            return  # nobody was ever rung -- nothing worth logging

        conversation = await db.get(Conversation, call.conversation_id)
        if conversation is None:
            return
        message = await create_message(
            db,
            conversation,
            call.caller_id,
            type="call",
            call_outcome=outcome,
            call_video=call.video,
            call_duration_seconds=duration,
        )
        member_ids = await get_member_user_ids(db, call.conversation_id)
        event = {"type": "message", "message": MessageOut.model_validate(message).model_dump(mode="json")}
        await manager.broadcast_to_users(json.dumps(event), member_ids)

    # --- errors ---

    async def _send_error(
        self, manager: ConnectionManager, user_id: uuid.UUID, call_id: uuid.UUID | None, reason: str
    ) -> None:
        event = {"type": "call", "action": "call:error", "call_id": str(call_id) if call_id else None, "reason": reason}
        await manager.send_to_user(json.dumps(event), user_id)
