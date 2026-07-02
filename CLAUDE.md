# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Patschat" — a phone-number/OTP group chat app with media attachments, avatars, and mesh WebRTC audio/video calls. FastAPI + async SQLAlchemy (Postgres) backend, vanilla JS frontend served as static files from the same process.

## Commands

```powershell
# Activate the venv first (Windows)
venv\Scripts\Activate.ps1

# Start Postgres (dev DB: group_chat, postgres/postgres on localhost:5432)
docker-compose up -d

# Run the app (serves frontend at http://localhost:8000)
uvicorn app.main:app --reload

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Tests (needs reachable Postgres; suite auto-creates its own group_chat_test DB)
python -m pytest
python -m pytest tests/test_messages.py                 # one file
python -m pytest tests/test_auth.py -k refresh          # by keyword
```

Dev deps: `pip install -r requirements-dev.txt`. There is no linter or formatter configured.

## Architecture

Feature-package layout under `app/`: each of `auth/`, `conversations/`, `messages/`, `media/`, `users/` has a `routes.py` (APIRouter, included in `app/main.py`) plus supporting modules (`deps.py`, `service.py`, etc.). Shared: `models.py` (all SQLAlchemy models), `schemas.py` (all Pydantic schemas), `config.py` (pydantic-settings, loads `.env`, see `.env.example`), `db.py` (async engine/session).

### Realtime — the core flow

Everything realtime goes over one websocket endpoint, `/ws` in `app/main.py`, authenticated via a `token` query param (`get_current_user_ws`). The handler dispatches inline:

- Payloads with `action: "call:*"` go to `CallManager` (`app/realtime/calls.py`) — WebRTC signaling and call lifecycle (ring/join/leave/end, logging a `call` message with outcome/duration on end).
- Payloads with `conversation_id` + (`text` | `media_id`) create a message via `app/messages/service.py` and broadcast it to all conversation members through `ConnectionManager` (`app/realtime/manager.py`).
- Errors are sent back as `{"type": "system", "text": ...}` frames rather than closing the socket. Failed WS auth accepts then closes with app-level code 4401.

**Both managers are in-memory singletons on a single process.** Running with multiple workers silently breaks message delivery and calls — do not add `--workers N`. The documented upgrade path (Redis pub/sub backplane) is in `ConnectionManager`'s docstring.

Message sending is WS-only; REST (`app/messages/routes.py`) is only for history pagination. Per-connection message rate limiting lives inline in the `/ws` handler.

### Auth

Phone number (E.164, normalized in `app/phone.py`) → OTP → JWT access token (30 min) + opaque refresh token (hashed in DB, 30 days). OTP providers are pluggable via `OTP_PROVIDER`: `dev` (returns the code in the API response — never in prod) or `twilio` (Twilio Verify owns the code lifecycle). DB tables `otp_requests` / `otp_verify_attempts` exist only for rate limiting (per-phone and per-IP), not code storage. Users are created implicitly on first successful OTP verify.

### Media & avatars

Upload via `POST /media/upload` first, then reference the returned `media_id` (in a WS message, or as a user/group avatar). Storage is pluggable via `STORAGE_BACKEND` (`app/media/storage.py`): `local` (writes to `media_uploads/`) or `s3` (boto3 imported lazily and **not** in requirements.txt). Access control in `app/media/deps.py` (`get_media_for_user`): message attachments are visible to conversation members; avatars are visible to any authenticated user. `/media/{id}` requires an Authorization header, so the frontend fetches blobs via JS rather than plain `<img src>`.

### Data model notes

- `users.avatar_media_id` ↔ `media.uploader_id` is a genuine circular FK — handled with `use_alter=True` in models and two-step migrations; test truncation uses `CASCADE` for the same reason.
- DMs are deduplicated by a unique sorted `dm_key` on `conversations` (find-or-create); groups have roles (`member`/`admin`) on `conversation_members`.
- Messages use soft delete (`deleted_at`) and `type` enum `text|system|image|file|call`.

### Tests

`tests/conftest.py` points the app at `group_chat_test` (created automatically), rebuilds the schema per session via `DROP SCHEMA public CASCADE` + `create_all` (migrations are not exercised), and truncates all tables between tests. It swaps in `NullPool` to avoid asyncpg cross-event-loop errors with `TestClient` — keep that if touching DB setup. Websocket tests use `TestClient.websocket_connect`; shared helpers (user/token/conversation factories) live in `tests/helpers.py`.

## Deployment

`DEPLOYMENT.md` is the authoritative production checklist (single-worker requirement, proxy-forwarded IPs for OTP rate limiting, HTTPS requirement for getUserMedia, known v1 limitations like no TURN server). Consult it before changing anything deployment-adjacent.
