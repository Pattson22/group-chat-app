from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from typing import Dict, List
import sqlite3
import json
import time
from collections import deque
from datetime import datetime

app = FastAPI()

# --- RATE LIMITING ---
RATE_LIMIT_MESSAGES = 5
RATE_LIMIT_WINDOW_SECONDS = 3.0

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('chat.db')
    cursor = conn.cursor()
    # Added a 'timestamp' column
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


# --- FRONTEND UI ---
html = """
<!DOCTYPE html>
<html>
    <head>
        <title>Project Chat</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0f2f5; margin: 0; padding: 20px; display: flex; justify-content: center; }
            .container { width: 100%; max-width: 600px; background: white; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); padding: 20px; }
            
            #chat-box { display: none; display: flex; flex-direction: column; height: 80vh; }
            #setup-box { text-align: center; margin-top: 50px; }
            
            h1, h2 { color: #1c1e21; margin-top: 0; }
            input[type="text"] { width: calc(100% - 24px); padding: 12px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 6px; font-size: 16px; }
            button { width: 100%; padding: 12px; cursor: pointer; background-color: #0866ff; color: white; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; }
            button:hover { background-color: #0054d1; }
            
            #messages-container { flex-grow: 1; overflow-y: auto; padding: 10px; background: #e4e6eb; border-radius: 8px; margin-bottom: 15px; }
            #messages { list-style-type: none; padding: 0; margin: 0; display: flex; flex-direction: column; }
            
            /* Chat Bubble Styling */
            .msg-wrapper { margin-bottom: 15px; display: flex; flex-direction: column; }
            .msg-wrapper.system { align-items: center; }
            .system-text { font-size: 12px; color: #65676b; background: #d8dadf; padding: 4px 10px; border-radius: 12px; }
            
            .msg-bubble { max-width: 80%; padding: 10px 14px; border-radius: 18px; position: relative; background-color: white; color: black; align-self: flex-start; border-bottom-left-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
            .msg-bubble.self { background-color: #0866ff; color: white; align-self: flex-end; border-bottom-left-radius: 18px; border-bottom-right-radius: 4px; }
            
            .msg-sender { font-size: 11px; font-weight: bold; margin-bottom: 4px; opacity: 0.8; }
            .msg-time { font-size: 10px; text-align: right; margin-top: 5px; opacity: 0.7; }
            
            .input-area { display: flex; gap: 10px; }
            .input-area input { margin-bottom: 0; width: auto; flex-grow: 1; }
            .input-area button { width: auto; padding: 10px 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div id="setup-box">
                <h1>Group Project Chat</h1>
                <input type="text" id="roomName" placeholder="Enter Room Name (e.g., biology-project)"/>
                <input type="text" id="userName" placeholder="Enter Your Name"/>
                <button onclick="joinRoom()">Join Chat</button>
            </div>

            <div id="chat-box" style="display: none;">
                <h2>Room: <span id="current-room"></span></h2>
                <div id="messages-container">
                    <ul id='messages'></ul>
                </div>
                <form class="input-area" action="" onsubmit="sendMessage(event)">
                    <input type="text" id="messageText" autocomplete="off" placeholder="Type a message..."/>
                    <button>Send</button>
                </form>
            </div>
        </div>

        <script>
            var ws = null;
            var userName = "";
            var roomName = "";

            function joinRoom() {
                roomName = document.getElementById("roomName").value;
                userName = document.getElementById("userName").value;
                
                if (!roomName || !userName) {
                    alert("Please enter both a room and a name.");
                    return;
                }

                document.getElementById("setup-box").style.display = "none";
                document.getElementById("chat-box").style.display = "flex";
                document.getElementById("current-room").textContent = roomName;

                ws = new WebSocket(`ws://${location.host}/ws/${roomName}/${userName}`);

                function escapeHtml(str) {
                    var div = document.createElement('div');
                    div.textContent = str;
                    return div.innerHTML;
                }

                ws.onmessage = function(event) {
                    var messages = document.getElementById('messages');
                    var wrapper = document.createElement('li');
                    wrapper.className = "msg-wrapper";

                    // Parse the incoming JSON data
                    var data = JSON.parse(event.data);
                    var safeText = escapeHtml(data.text);
                    var safeTime = escapeHtml(data.time);

                    if (data.type === "system") {
                        wrapper.classList.add("system");
                        wrapper.innerHTML = `<span class="system-text">${safeText}</span>`;
                    } else {
                        var bubble = document.createElement('div');
                        bubble.className = "msg-bubble";

                        // Check if the message is from the current user
                        if (data.name === userName && data.type !== "history") {
                            bubble.classList.add("self");
                            bubble.innerHTML = `<div>${safeText}</div><div class="msg-time">${safeTime}</div>`;
                        } else {
                            var safeName = escapeHtml(data.name);
                            var displayName = data.type === "history" ? safeName + " (Saved)" : safeName;
                            bubble.innerHTML = `<div class="msg-sender">${displayName}</div><div>${safeText}</div><div class="msg-time">${safeTime}</div>`;
                        }
                        wrapper.appendChild(bubble);
                    }
                    
                    messages.appendChild(wrapper);
                    
                    // Auto-scroll to the bottom
                    var container = document.getElementById("messages-container");
                    container.scrollTop = container.scrollHeight;
                };
            }
            
            function sendMessage(event) {
                var input = document.getElementById("messageText");
                if (ws && input.value) {
                    ws.send(input.value);
                    input.value = '';
                }
                event.preventDefault();
            }
        </script>
    </body>
</html>
"""


# --- WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_name: str):
        await websocket.accept()
        if room_name not in self.active_connections:
            self.active_connections[room_name] = []
        self.active_connections[room_name].append(websocket)

    def disconnect(self, websocket: WebSocket, room_name: str):
        if room_name in self.active_connections:
            self.active_connections[room_name].remove(websocket)
            if not self.active_connections[room_name]:
                del self.active_connections[room_name]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str, room_name: str):
        if room_name not in self.active_connections:
            return
        dead_connections = []
        for connection in self.active_connections[room_name]:
            try:
                await connection.send_text(message)
            except Exception:
                dead_connections.append(connection)
        for connection in dead_connections:
            self.disconnect(connection, room_name)

manager = ConnectionManager()


# --- ROUTES ---
@app.get("/")
async def get():
    return HTMLResponse(html)

@app.websocket("/ws/{room_name}/{client_name}")
async def websocket_endpoint(websocket: WebSocket, room_name: str, client_name: str):
    await manager.connect(websocket, room_name)
    
    # 1. Fetch chat history and send as JSON
    conn = sqlite3.connect('chat.db')
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
            # RATE_LIMIT_MESSAGES within RATE_LIMIT_WINDOW_SECONDS.
            send_time = time.monotonic()
            while message_times and send_time - message_times[0] > RATE_LIMIT_WINDOW_SECONDS:
                message_times.popleft()
            if len(message_times) >= RATE_LIMIT_MESSAGES:
                warning = json.dumps({"type": "system", "text": "You're sending messages too fast. Please slow down."})
                await manager.send_personal_message(warning, websocket)
                continue
            message_times.append(send_time)

            # Get current time
            now = datetime.now().strftime("%I:%M %p")

            # 3. Save to database with timestamp
            conn = sqlite3.connect('chat.db')
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