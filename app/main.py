import json
import sqlite3
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth.routes import router as auth_router
from app.config import settings
from app.realtime.manager import ConnectionManager

app = FastAPI()
app.include_router(auth_router)

FRONTEND_DIR = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(settings.sqlite_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT,
            name TEXT,
            message TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()


init_db()


manager = ConnectionManager()


# --- ROUTES ---
@app.get("/")
async def get():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.websocket("/ws/{room_name}/{client_name}")
async def websocket_endpoint(websocket: WebSocket, room_name: str, client_name: str):
    await manager.connect(websocket, room_name)

    # 1. Fetch chat history and send as JSON
    conn = sqlite3.connect(settings.sqlite_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name, message, timestamp FROM messages WHERE room=? ORDER BY id ASC", (room_name,))
    history = cursor.fetchall()

    for row in history:
        saved_name, saved_msg, saved_time = row
        history_data = json.dumps({"type": "history", "name": saved_name, "text": saved_msg, "time": saved_time})
        await manager.send_personal_message(history_data, websocket)
    conn.close()

    # 2. Announce new user using JSON
    system_msg = json.dumps({"type": "system", "text": f"⚡ {client_name} joined the room"})
    await manager.broadcast(system_msg, room_name)

    message_times = deque()

    try:
        while True:
            data = await websocket.receive_text()

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

            # Get current time
            now = datetime.now().strftime("%I:%M %p")

            # 3. Save to database with timestamp
            conn = sqlite3.connect(settings.sqlite_path)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO messages (room, name, message, timestamp) VALUES (?, ?, ?, ?)", (room_name, client_name, data, now))
            conn.commit()
            conn.close()

            # 4. Broadcast the message as JSON
            msg_data = json.dumps({"type": "message", "name": client_name, "text": data, "time": now})
            await manager.broadcast(msg_data, room_name)

    except WebSocketDisconnect:
        manager.disconnect(websocket, room_name)
        leave_msg = json.dumps({"type": "system", "text": f"🏃 {client_name} left the room"})
        await manager.broadcast(leave_msg, room_name)
