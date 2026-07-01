let session = null; // { accessToken, refreshToken, user }
let ws = null;
let conversations = [];
let currentConversation = null;
let oldestLoadedMessageId = null;
let hasMoreOlder = false;
const mediaUrlCache = new Map(); // media_id -> object URL

// --- view management ---

function showView(id) {
    document.querySelectorAll('.screen').forEach((el) => {
        el.hidden = el.id !== id;
    });
}

function isViewVisible(id) {
    return document.getElementById(id).hidden === false;
}

// --- session persistence ---

function saveSession() {
    localStorage.setItem('session', JSON.stringify(session));
}

function clearSession() {
    session = null;
    localStorage.removeItem('session');
}

// --- API client ---

async function tryRefresh() {
    try {
        const resp = await fetch('/auth/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: session.refreshToken }),
        });
        if (!resp.ok) return false;
        const data = await resp.json();
        session.accessToken = data.access_token;
        session.refreshToken = data.refresh_token;
        session.user = data.user;
        saveSession();
        return true;
    } catch (e) {
        return false;
    }
}

async function apiFetch(path, options = {}) {
    options.headers = options.headers || {};
    if (session && session.accessToken) {
        options.headers['Authorization'] = `Bearer ${session.accessToken}`;
    }
    let resp = await fetch(path, options);
    if (resp.status === 401 && session && session.refreshToken) {
        const refreshed = await tryRefresh();
        if (refreshed) {
            options.headers['Authorization'] = `Bearer ${session.accessToken}`;
            resp = await fetch(path, options);
        }
    }
    return resp;
}

async function apiJson(path, method, body) {
    return apiFetch(path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
}

// --- auth flow ---

let pendingPhone = '';

document.getElementById('sendCodeBtn').addEventListener('click', async () => {
    const phone = document.getElementById('phoneInput').value.trim();
    const errorEl = document.getElementById('phoneError');
    errorEl.textContent = '';
    if (!phone) {
        errorEl.textContent = 'Enter a phone number.';
        return;
    }

    const resp = await fetch('/auth/request-otp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number: phone }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        errorEl.textContent = data.detail || 'Failed to send code.';
        return;
    }

    pendingPhone = phone;
    document.getElementById('otpPhoneLabel').textContent = phone;
    const hint = document.getElementById('devCodeHint');
    if (data.dev_otp_code) {
        hint.hidden = false;
        hint.textContent = `Dev mode: your code is ${data.dev_otp_code}`;
    } else {
        hint.hidden = true;
    }
    document.getElementById('otpInput').value = '';
    document.getElementById('otpError').textContent = '';
    showView('view-otp');
});

document.getElementById('backToPhoneBtn').addEventListener('click', () => {
    showView('view-phone');
});

document.getElementById('verifyCodeBtn').addEventListener('click', async () => {
    const code = document.getElementById('otpInput').value.trim();
    const errorEl = document.getElementById('otpError');
    errorEl.textContent = '';
    if (!code) {
        errorEl.textContent = 'Enter the code.';
        return;
    }

    const resp = await fetch('/auth/verify-otp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number: pendingPhone, code }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        errorEl.textContent = data.detail || 'Invalid code.';
        return;
    }

    session = { accessToken: data.access_token, refreshToken: data.refresh_token, user: data.user };
    saveSession();
    await afterLogin();
});

document.getElementById('saveProfileBtn').addEventListener('click', async () => {
    const name = document.getElementById('displayNameInput').value.trim();
    const errorEl = document.getElementById('profileError');
    errorEl.textContent = '';
    if (!name) {
        errorEl.textContent = 'Enter a name.';
        return;
    }

    const resp = await apiJson('/auth/me', 'PATCH', { display_name: name });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        errorEl.textContent = data.detail || 'Failed to save name.';
        return;
    }

    session.user = data;
    saveSession();
    await enterInbox();
});

document.getElementById('logoutBtn').addEventListener('click', async () => {
    if (session) {
        try {
            await fetch('/auth/logout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: session.refreshToken }),
            });
        } catch (e) {
            // best-effort; proceed with local logout regardless
        }
    }
    if (ws) {
        ws.close();
        ws = null;
    }
    clearSession();
    currentConversation = null;
    conversations = [];
    showView('view-phone');
});

async function afterLogin() {
    if (!session.user.display_name) {
        showView('view-profile');
    } else {
        await enterInbox();
    }
}

// --- realtime ---

function connectWs() {
    if (ws) {
        ws.close();
    }
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${protocol}://${location.host}/ws?token=${encodeURIComponent(session.accessToken)}`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'message') {
            handleIncomingMessage(data.message);
        } else if (data.type === 'system') {
            handleSystemEvent(data.text);
        }
    };

    ws.onclose = (event) => {
        if (event.code === 4401) {
            clearSession();
            showView('view-phone');
        }
    };
}

async function handleIncomingMessage(msg) {
    updateConversationPreview(msg);

    if (currentConversation && msg.conversation_id === currentConversation.id) {
        const el = await buildMessageElement(msg);
        document.getElementById('messages').appendChild(el);
        scrollToBottom();
    }
}

function handleSystemEvent(text) {
    if (currentConversation) {
        appendSystemNotice(text);
    }
}

function updateConversationPreview(msg) {
    const idx = conversations.findIndex((c) => c.id === msg.conversation_id);
    if (idx === -1) {
        // Conversation we don't know about yet (e.g. just added to a group).
        loadConversations();
        return;
    }
    conversations[idx].last_message_at = msg.created_at;
    const [conv] = conversations.splice(idx, 1);
    conversations.unshift(conv);
    if (isViewVisible('view-inbox')) {
        renderConversationList();
    }
}

// --- inbox ---

async function enterInbox() {
    document.getElementById('meLabel').textContent = session.user.display_name || session.user.phone_number;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        connectWs();
    }
    showView('view-inbox');
    await loadConversations();
}

async function loadConversations() {
    const resp = await apiFetch('/conversations');
    if (!resp.ok) return;
    conversations = await resp.json();
    renderConversationList();
}

function conversationDisplayName(conv) {
    if (conv.type === 'group') {
        return conv.name || 'Group';
    }
    const other = conv.members.find((m) => m.user_id !== session.user.id);
    return other ? other.display_name || other.phone_number : 'You';
}

function conversationPreviewLabel(conv) {
    if (!conv.last_message_at) return 'No messages yet';
    return `Last active ${formatTime(conv.last_message_at)}`;
}

function renderConversationList() {
    const list = document.getElementById('conversationList');
    list.innerHTML = '';

    if (conversations.length === 0) {
        const li = document.createElement('li');
        li.className = 'empty-state';
        li.textContent = 'No conversations yet. Start a new chat!';
        list.appendChild(li);
        return;
    }

    for (const conv of conversations) {
        const li = document.createElement('li');
        li.className = 'conversation-item';

        const nameDiv = document.createElement('div');
        nameDiv.className = 'conversation-name';
        nameDiv.textContent = conversationDisplayName(conv);

        const previewDiv = document.createElement('div');
        previewDiv.className = 'conversation-preview';
        previewDiv.textContent = conversationPreviewLabel(conv);

        li.appendChild(nameDiv);
        li.appendChild(previewDiv);
        li.addEventListener('click', () => openConversation(conv));
        list.appendChild(li);
    }
}

document.getElementById('newChatBtn').addEventListener('click', () => {
    document.getElementById('newGroupPanel').hidden = true;
    document.getElementById('newChatPanel').hidden = false;
    document.getElementById('newChatPhoneInput').value = '';
    document.getElementById('newChatError').textContent = '';
});

document.getElementById('newChatCancelBtn').addEventListener('click', () => {
    document.getElementById('newChatPanel').hidden = true;
});

document.getElementById('newChatSubmitBtn').addEventListener('click', async () => {
    const phone = document.getElementById('newChatPhoneInput').value.trim();
    const errorEl = document.getElementById('newChatError');
    errorEl.textContent = '';
    if (!phone) {
        errorEl.textContent = 'Enter a phone number.';
        return;
    }

    const lookupResp = await apiFetch(`/users/lookup?phone_number=${encodeURIComponent(phone)}`);
    const lookupData = await lookupResp.json().catch(() => ({}));
    if (!lookupResp.ok) {
        errorEl.textContent = lookupData.detail || 'User not found.';
        return;
    }
    if (lookupData.id === session.user.id) {
        errorEl.textContent = "That's your own number.";
        return;
    }

    const dmResp = await apiJson('/conversations/dm', 'POST', { other_user_id: lookupData.id });
    const dmData = await dmResp.json().catch(() => ({}));
    if (!dmResp.ok) {
        errorEl.textContent = dmData.detail || 'Failed to start chat.';
        return;
    }

    document.getElementById('newChatPanel').hidden = true;
    await loadConversations();
    openConversation(dmData);
});

document.getElementById('newGroupBtn').addEventListener('click', () => {
    document.getElementById('newChatPanel').hidden = true;
    document.getElementById('newGroupPanel').hidden = false;
    document.getElementById('newGroupNameInput').value = '';
    document.getElementById('newGroupMembersInput').value = '';
    document.getElementById('newGroupError').textContent = '';
});

document.getElementById('newGroupCancelBtn').addEventListener('click', () => {
    document.getElementById('newGroupPanel').hidden = true;
});

document.getElementById('newGroupSubmitBtn').addEventListener('click', async () => {
    const name = document.getElementById('newGroupNameInput').value.trim();
    const phonesRaw = document.getElementById('newGroupMembersInput').value.trim();
    const errorEl = document.getElementById('newGroupError');
    errorEl.textContent = '';
    if (!name) {
        errorEl.textContent = 'Enter a group name.';
        return;
    }

    const phones = phonesRaw
        .split(',')
        .map((p) => p.trim())
        .filter(Boolean);
    const memberIds = [];
    for (const phone of phones) {
        const lookupResp = await apiFetch(`/users/lookup?phone_number=${encodeURIComponent(phone)}`);
        if (!lookupResp.ok) {
            errorEl.textContent = `Could not find a user with phone ${phone}.`;
            return;
        }
        const lookupData = await lookupResp.json();
        if (lookupData.id !== session.user.id) {
            memberIds.push(lookupData.id);
        }
    }

    const resp = await apiJson('/conversations/group', 'POST', { name, member_ids: memberIds });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        errorEl.textContent = data.detail || 'Failed to create group.';
        return;
    }

    document.getElementById('newGroupPanel').hidden = true;
    await loadConversations();
    openConversation(data);
});

// --- conversation view ---

async function openConversation(conv) {
    currentConversation = conv;
    oldestLoadedMessageId = null;
    hasMoreOlder = false;
    document.getElementById('chatTitle').textContent = conversationDisplayName(conv);
    document.getElementById('messages').innerHTML = '';
    document.getElementById('chatError').textContent = '';
    showView('view-chat');
    await loadMessages();
}

document.getElementById('backToInboxBtn').addEventListener('click', async () => {
    currentConversation = null;
    showView('view-inbox');
    await loadConversations();
});

async function loadMessages(before) {
    let url = `/conversations/${currentConversation.id}/messages?limit=30`;
    if (before) url += `&before=${before}`;

    const resp = await apiFetch(url);
    if (!resp.ok) return;
    const page = await resp.json();

    hasMoreOlder = page.has_more;
    document.getElementById('loadOlderBtn').hidden = !hasMoreOlder;
    if (page.next_cursor) {
        oldestLoadedMessageId = page.next_cursor;
    }

    const list = document.getElementById('messages');
    const fragment = document.createDocumentFragment();
    for (const msg of page.items) {
        fragment.appendChild(await buildMessageElement(msg));
    }

    if (before) {
        const container = document.getElementById('messagesContainer');
        const prevHeight = container.scrollHeight;
        list.prepend(fragment);
        container.scrollTop = container.scrollHeight - prevHeight;
    } else {
        list.appendChild(fragment);
        scrollToBottom();
    }
}

document.getElementById('loadOlderBtn').addEventListener('click', () => {
    if (oldestLoadedMessageId) {
        loadMessages(oldestLoadedMessageId);
    }
});

function scrollToBottom() {
    const container = document.getElementById('messagesContainer');
    container.scrollTop = container.scrollHeight;
}

function senderDisplayName(userId) {
    if (!currentConversation) return userId;
    const member = currentConversation.members.find((m) => m.user_id === userId);
    return member ? member.display_name || member.phone_number : userId;
}

function formatTime(isoString) {
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function appendSystemNotice(text) {
    const wrapper = document.createElement('li');
    wrapper.className = 'msg-wrapper system';
    const span = document.createElement('span');
    span.className = 'system-text';
    span.textContent = text;
    wrapper.appendChild(span);
    document.getElementById('messages').appendChild(wrapper);
    scrollToBottom();
}

async function buildMessageElement(msg) {
    const wrapper = document.createElement('li');
    wrapper.className = 'msg-wrapper';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    const isSelf = msg.sender_id === session.user.id;
    if (isSelf) bubble.classList.add('self');

    if (!isSelf) {
        const senderDiv = document.createElement('div');
        senderDiv.className = 'msg-sender';
        senderDiv.textContent = senderDisplayName(msg.sender_id);
        bubble.appendChild(senderDiv);
    }

    if (msg.type === 'image' || msg.type === 'file') {
        bubble.appendChild(await buildMediaElement(msg));
    } else {
        const textDiv = document.createElement('div');
        // textContent never interprets its argument as markup, so message
        // bodies render as inert text even if they contain "<script>" etc.
        textDiv.textContent = msg.body || '';
        bubble.appendChild(textDiv);
    }

    const timeDiv = document.createElement('div');
    timeDiv.className = 'msg-time';
    timeDiv.textContent = formatTime(msg.created_at);
    bubble.appendChild(timeDiv);

    wrapper.appendChild(bubble);
    return wrapper;
}

async function fetchMediaBlobUrl(mediaId) {
    if (mediaUrlCache.has(mediaId)) return mediaUrlCache.get(mediaId);
    // /media/{id} requires an Authorization header, which a plain <img src>
    // can't send -- fetch it ourselves and hand the bubble an object URL.
    const resp = await apiFetch(`/media/${mediaId}`);
    if (!resp.ok) return null;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    mediaUrlCache.set(mediaId, url);
    return url;
}

async function buildMediaElement(msg) {
    const url = await fetchMediaBlobUrl(msg.media_id);
    if (!url) {
        const div = document.createElement('div');
        div.textContent = '[unable to load attachment]';
        return div;
    }
    if (msg.type === 'image') {
        const img = document.createElement('img');
        img.src = url;
        img.alt = 'attachment';
        return img;
    }
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.className = 'msg-file-link';
    link.textContent = '📄 Download attachment';
    return link;
}

document.getElementById('messageForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = document.getElementById('messageText');
    const text = input.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ conversation_id: currentConversation.id, text }));
    input.value = '';
});

document.getElementById('attachBtn').addEventListener('click', () => {
    document.getElementById('fileInput').click();
});

document.getElementById('fileInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file || !currentConversation) return;

    const errorEl = document.getElementById('chatError');
    errorEl.textContent = '';

    const formData = new FormData();
    formData.append('file', file);

    const resp = await apiFetch('/media/upload', { method: 'POST', body: formData });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        errorEl.textContent = data.detail || 'Upload failed.';
        return;
    }

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        errorEl.textContent = 'Not connected, try again.';
        return;
    }
    ws.send(JSON.stringify({ conversation_id: currentConversation.id, media_id: data.id }));
});

// --- startup ---

window.addEventListener('DOMContentLoaded', async () => {
    const stored = localStorage.getItem('session');
    if (stored) {
        session = JSON.parse(stored);
        const resp = await apiFetch('/auth/me');
        if (resp.ok) {
            session.user = await resp.json();
            saveSession();
            await afterLogin();
            return;
        }
        clearSession();
    }
    showView('view-phone');
});
