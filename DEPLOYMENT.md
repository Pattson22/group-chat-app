# Patschat — Production Deployment Checklist

Everything here is specific to this codebase. Items marked **BLOCKER** will
break the app (or its security) in production if skipped; the rest are
strongly recommended before opening it to real users.

---

## 1. Secrets & configuration (`.env`)

All settings load from `.env` via `app/config.py`. Copy `.env.example` and
work through it — never commit the real `.env` (already gitignored).

- [ ] **BLOCKER — `JWT_SECRET`**: still defaults to `dev-secret-change-me`.
      Anyone who knows the default can mint valid access tokens for any
      user. Generate a proper one (48+ bytes; the test suite already warns
      that short HMAC keys are below the RFC 7518 minimum):
      ```
      python -c "import secrets; print(secrets.token_urlsafe(48))"
      ```
- [ ] **BLOCKER — `DATABASE_URL`**: point at the production Postgres with a
      strong password. The dev default (`postgres:postgres@localhost`) must
      not survive.
- [ ] **BLOCKER — `OTP_PROVIDER=twilio`** plus `TWILIO_ACCOUNT_SID`,
      `TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID`. The default `dev`
      provider returns the OTP code in the API response
      (`RequestOtpOut.dev_otp_code`) — in production that means anyone can
      log in as any phone number without owning it.
- [ ] **`STORAGE_BACKEND=s3`** with `S3_BUCKET`, `S3_REGION` (and
      `S3_ENDPOINT_URL` for R2/B2/MinIO). Local-disk storage ties every
      upload — including all avatars — to one machine's filesystem.
      **Note:** `boto3` is imported lazily by `S3Storage` and is *not* in
      `requirements.txt`; add it there when enabling S3.
- [ ] Review rate-limit numbers for real-world traffic
      (`otp_request_limit_per_phone=3`, `otp_request_limit_per_ip=10` per
      10 min; `rate_limit_messages=5` per 3s; `media_max_size_bytes=10MB`;
      `call_max_participants=6`).

## 2. TLS / reverse proxy

The app itself speaks plain HTTP; put it behind Caddy or Nginx for TLS.

- [ ] **BLOCKER — HTTPS**: browsers only allow `getUserMedia` (camera/mic)
      on secure origins, so audio/video calling silently cannot work at all
      over plain HTTP outside localhost. The frontend already picks
      `wss://` automatically when the page is served over HTTPS
      (`app.js` → `connectWs`).
- [ ] **BLOCKER — forwarded client IPs**: OTP rate limiting keys on
      `request.client.host` (`app/auth/routes.py`). Behind a proxy that is
      the *proxy's* IP, so the per-IP limit of 10 OTP requests / 10 min
      would apply to **all users combined** — effectively a signup cap of
      10 users per 10 minutes. Run uvicorn with
      `--proxy-headers --forwarded-allow-ips=<proxy IP>` and make the
      proxy set `X-Forwarded-For`.
- [ ] **WebSocket upgrade headers**: Nginx needs the standard
      `Upgrade`/`Connection` header block on the `/ws` location (Caddy
      handles this automatically). Also disable/raise proxy read timeouts
      for `/ws` so idle chat connections aren't cut every 60s.
- [ ] **Body size limit**: Nginx's default `client_max_body_size` is 1MB,
      which would break media/avatar uploads (app allows 10MB). Raise it to
      match.

## 3. Process model — read before scaling anything

- [ ] **BLOCKER — exactly one worker process.** Realtime state is
      in-memory: `ConnectionManager.active_connections` and
      `CallManager.calls` (`app/realtime/`). With 2+ workers, users land on
      different processes and simply never receive each other's messages
      or calls — no errors, just silence. Run:
      ```
      uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=...
      ```
      Do **not** add `--workers N` and do not front it with
      `gunicorn -w N`. The documented upgrade path (Redis pub/sub
      backplane, see `ConnectionManager`'s docstring) must be built before
      horizontal scaling.
- [ ] Run under a supervisor (systemd unit, or Docker with
      `restart: unless-stopped`) so crashes restart the process. Note that
      a restart drops all live websockets and any in-progress calls —
      clients reconnect on next page interaction, calls must be re-dialed.

## 4. Database

- [ ] Provision production Postgres (managed is fine; the app uses
      standard asyncpg + `pool_pre_ping`).
- [ ] Run `alembic upgrade head` against it before first boot (5
      migrations, `alembic/versions/`). The production DB starts empty —
      no dev/test data (Alice, Bob, etc.) exists anywhere in the repo.
- [ ] Set up automated backups (dumps or provider snapshots). If staying
      on local-disk media storage against advice, back up
      `media_uploads/` too — avatars and attachments live there.
- [ ] Never point production at the dev Docker Postgres or reuse its
      `postgres_data` volume.

## 5. Known functional limitations (accepted for v1 — verify you accept them too)

- [ ] **No TURN server** — WebRTC uses public STUN only
      (`app/frontend/calls.js`). Users behind symmetric NATs / strict
      corporate firewalls will fail to connect calls (signaling works,
      media never flows). Fix later by running coturn and adding it to
      `ICE_SERVERS`.
- [ ] **Mesh calls cap at 6 participants** (`call_max_participants`) and
      video quality degrades with participant count — every peer uploads
      to every other peer. An SFU is the eventual fix, not a config change.
- [ ] **Unclean disconnects during calls** are only detected client-side
      via `RTCPeerConnection.connectionState`; the server notices a dead
      websocket on next write. No heartbeat/reaper exists.
- [ ] **No message delivery receipts, typing indicators, push
      notifications, or offline message queue signal** — offline users
      simply see messages on next load.

## 6. Pre-launch verification

- [ ] Full test suite green: `python -m pytest` (61 tests; needs a
      reachable Postgres — it creates its own `group_chat_test` database).
- [ ] **Real-device call test**: every call verification so far used
      stubbed media streams — a live audio/video connection between two
      physical devices on different networks has *never* been confirmed.
      Do this once over the real HTTPS deployment before announcing calls
      as a feature.
- [ ] Real-SMS signup test: one full phone → Twilio SMS → verify → chat
      round-trip on the production domain.
- [ ] Confirm the dev OTP hint is gone: `POST /auth/request-otp` must
      return `{"dev_otp_code": null}` in production.
- [ ] Upload an avatar + an image attachment and confirm they survive an
      app restart (validates the storage backend really is S3).

## 7. Post-launch (soon after, not blocking)

- [ ] Add a `/health` endpoint and uptime monitoring.
- [ ] Aggregate server logs somewhere queryable; log OTP failures and
      auth errors for abuse spotting.
- [ ] Error tracking (e.g. Sentry) for both backend and the frontend JS.
- [ ] Decide a media retention/orphan-cleanup policy — replaced avatars
      and unsent uploads currently accumulate forever by design.
- [ ] Consider CAPTCHA or stricter throttling on `/auth/request-otp` if
      SMS costs spike (each request costs real Twilio money).
