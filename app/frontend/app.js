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
