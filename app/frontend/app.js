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

// --- small UI helpers: loading buttons, error shake, skeletons ---

function setLoading(button, isLoading) {
    button.disabled = isLoading;
    button.classList.toggle('is-loading', isLoading);
}

function showError(errorEl, message, inputEl) {
    errorEl.textContent = message;
    restartAnimation(errorEl, 'shake');
    if (inputEl) restartAnimation(inputEl, 'shake');
}

function restartAnimation(el, className) {
    el.classList.remove(className);
    // Force a reflow so re-adding the class restarts the CSS animation
    // even if it was already applied moments ago.
    void el.offsetWidth;
    el.classList.add(className);
}

function renderSkeletonRows(container, count) {
    container.innerHTML = '';
    for (let i = 0; i < count; i++) {
        const row = document.createElement('li');
        row.className = 'skeleton skeleton-row';
        row.style.animationDelay = `${i * 60}ms`;
        container.appendChild(row);
    }
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

document.getElementById('sendCodeBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const phoneInput = document.getElementById('phoneInput');
    const phone = phoneInput.value.trim();
    const errorEl = document.getElementById('phoneError');
    errorEl.textContent = '';
    if (!phone) {
        showError(errorEl, 'Enter a phone number.', phoneInput);
        return;
    }

    setLoading(btn, true);
    try {
        const resp = await fetch('/auth/request-otp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone_number: phone }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showError(errorEl, data.detail || 'Failed to send code.', phoneInput);
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
    } finally {
        setLoading(btn, false);
    }
});

document.getElementById('backToPhoneBtn').addEventListener('click', () => {
    showView('view-phone');
});

document.getElementById('verifyCodeBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const codeInput = document.getElementById('otpInput');
    const code = codeInput.value.trim();
    const errorEl = document.getElementById('otpError');
    errorEl.textContent = '';
    if (!code) {
        showError(errorEl, 'Enter the code.', codeInput);
        return;
    }

    setLoading(btn, true);
    try {
        const resp = await fetch('/auth/verify-otp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone_number: pendingPhone, code }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showError(errorEl, data.detail || 'Invalid code.', codeInput);
            return;
        }

        session = { accessToken: data.access_token, refreshToken: data.refresh_token, user: data.user };
        saveSession();
        await afterLogin();
    } finally {
        setLoading(btn, false);
    }
});

function openProfileScreen(mode) {
    // mode: 'setup' (first login, no way back yet) or 'edit' (revisited
    // later via the inbox avatar, with a name already on file).
    document.getElementById('displayNameInput').value = mode === 'edit' ? session.user.display_name || '' : '';
    document.getElementById('profileError').textContent = '';
    document.getElementById('avatarError').textContent = '';
    document.getElementById('backToInboxFromProfileBtn').hidden = mode !== 'edit';
    renderAvatar(
        document.getElementById('profileAvatarPreview'),
        session.user.avatar_media_id,
        session.user.display_name || session.user.phone_number
    );
    showView('view-profile');
}

document.getElementById('myAvatarBtn').addEventListener('click', () => {
    openProfileScreen('edit');
});

document.getElementById('backToInboxFromProfileBtn').addEventListener('click', async () => {
    await enterInbox();
});

document.getElementById('avatarPickerBtn').addEventListener('click', () => {
    document.getElementById('avatarInput').click();
});

document.getElementById('avatarInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;

    const errorEl = document.getElementById('avatarError');
    errorEl.textContent = '';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await apiFetch('/auth/me/avatar', { method: 'POST', body: formData });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showError(errorEl, data.detail || 'Failed to upload photo.');
            return;
        }
        session.user = data;
        saveSession();
        await renderAvatar(
            document.getElementById('profileAvatarPreview'),
            session.user.avatar_media_id,
            session.user.display_name || session.user.phone_number
        );
    } catch (err) {
        showError(errorEl, 'Failed to upload photo.');
    }
});

document.getElementById('saveProfileBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const nameInput = document.getElementById('displayNameInput');
    const name = nameInput.value.trim();
    const errorEl = document.getElementById('profileError');
    errorEl.textContent = '';
    if (!name) {
        showError(errorEl, 'Enter a name.', nameInput);
        return;
    }

    setLoading(btn, true);
    try {
        const resp = await apiJson('/auth/me', 'PATCH', { display_name: name });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showError(errorEl, data.detail || 'Failed to save name.', nameInput);
            return;
        }

        session.user = data;
        saveSession();
        await enterInbox();
    } finally {
        setLoading(btn, false);
    }
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
    conversations = [];
    resetChatPanel();
    showView('view-phone');
});

async function afterLogin() {
    if (!session.user.display_name) {
        openProfileScreen('setup');
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
        } else if (data.type === 'call') {
            handleCallEvent(data);
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
    // The broadcast MessageOut is a superset of the preview shape, so it
    // can stand in for last_message directly.
    conversations[idx].last_message = msg;
    const [conv] = conversations.splice(idx, 1);
    conversations.unshift(conv);
    if (isViewVisible('view-main')) {
        renderConversationList();
    }
}

// --- avatars ---

function initialsFor(name) {
    if (!name) return '?';
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
}

// Renders either the fetched photo (as an <img>) or a flat initials
// fallback into `el`, which is expected to already carry avatar/avatar-*
// sizing classes -- this only ever adds/removes the avatar-initials class.
async function renderAvatar(el, mediaId, label) {
    if (mediaId) {
        const url = await fetchMediaBlobUrl(mediaId);
        if (url) {
            el.classList.remove('avatar-initials');
            el.innerHTML = '';
            const img = document.createElement('img');
            img.src = url;
            img.alt = '';
            el.appendChild(img);
            return;
        }
    }
    el.classList.add('avatar-initials');
    el.textContent = initialsFor(label);
}

function conversationAvatarInfo(conv) {
    if (conv.type === 'group') {
        return { mediaId: conv.avatar_media_id, label: conv.name || 'Group' };
    }
    const other = conv.members.find((m) => m.user_id !== session.user.id);
    return other
        ? { mediaId: other.avatar_media_id, label: other.display_name || other.phone_number }
        : { mediaId: null, label: 'You' };
}

// --- main view (sidebar + chat panel) ---

async function enterInbox() {
    document.getElementById('meLabel').textContent = session.user.display_name || session.user.phone_number;
    renderAvatar(document.getElementById('myAvatar'), session.user.avatar_media_id, session.user.display_name || session.user.phone_number);
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        connectWs();
    }
    showView('view-main');
    await loadConversations();
}

function resetChatPanel() {
    currentConversation = null;
    document.getElementById('chatArea').hidden = true;
    document.getElementById('chatEmpty').hidden = false;
    document.getElementById('appShell').classList.remove('chat-open');
}

async function loadConversations() {
    renderSkeletonRows(document.getElementById('conversationList'), 4);
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

function memberDisplayName(conv, userId) {
    const member = conv.members.find((m) => m.user_id === userId);
    return member ? member.display_name || member.phone_number : 'Unknown';
}

function messagePreviewText(msg) {
    if (msg.type === 'image') return 'Photo';
    if (msg.type === 'file') return 'Attachment';
    if (msg.type === 'call') {
        if (msg.call_outcome === 'missed') return msg.call_video ? 'Missed video call' : 'Missed call';
        return msg.call_video ? 'Video call' : 'Voice call';
    }
    return msg.body || '';
}

function conversationPreviewLabel(conv) {
    const last = conv.last_message;
    if (!last) {
        return conv.type === 'group' ? `Group · ${conv.members.length} members` : 'No messages yet';
    }
    let prefix = '';
    if (last.sender_id === session.user.id) {
        prefix = 'You: ';
    } else if (conv.type === 'group') {
        prefix = `${memberDisplayName(conv, last.sender_id)}: `;
    }
    return prefix + messagePreviewText(last);
}

let conversationFilter = '';

function renderConversationList() {
    const list = document.getElementById('conversationList');
    list.innerHTML = '';

    const visible = conversations.filter((conv) =>
        conversationDisplayName(conv).toLowerCase().includes(conversationFilter)
    );

    if (visible.length === 0) {
        const li = document.createElement('li');
        li.className = 'empty-state';
        li.textContent = conversationFilter
            ? 'No chats match your search.'
            : 'No conversations yet. Start a new chat!';
        list.appendChild(li);
        return;
    }

    visible.forEach((conv, index) => {
        const li = document.createElement('li');
        li.className = 'conversation-item';
        if (currentConversation && conv.id === currentConversation.id) {
            li.classList.add('active');
        }
        li.style.animationDelay = `${Math.min(index, 10) * 40}ms`;

        const avatarDiv = document.createElement('div');
        avatarDiv.className = 'avatar avatar-md avatar-initials';
        const avatarInfo = conversationAvatarInfo(conv);
        renderAvatar(avatarDiv, avatarInfo.mediaId, avatarInfo.label);

        const textDiv = document.createElement('div');
        textDiv.className = 'conversation-text';

        const topDiv = document.createElement('div');
        topDiv.className = 'conversation-top';

        const nameDiv = document.createElement('div');
        nameDiv.className = 'conversation-name';
        nameDiv.textContent = conversationDisplayName(conv);
        topDiv.appendChild(nameDiv);

        if (conv.last_message_at) {
            const timeDiv = document.createElement('div');
            timeDiv.className = 'conversation-time';
            timeDiv.textContent = formatTime(conv.last_message_at);
            topDiv.appendChild(timeDiv);
        }

        const previewDiv = document.createElement('div');
        previewDiv.className = 'conversation-preview';
        previewDiv.textContent = conversationPreviewLabel(conv);

        textDiv.appendChild(topDiv);
        textDiv.appendChild(previewDiv);
        li.appendChild(avatarDiv);
        li.appendChild(textDiv);
        li.addEventListener('click', () => openConversation(conv));
        list.appendChild(li);
    });
}

document.getElementById('searchInput').addEventListener('input', (e) => {
    conversationFilter = e.target.value.trim().toLowerCase();
    renderConversationList();
});

document.getElementById('newChatBtn').addEventListener('click', () => {
    document.getElementById('newGroupPanel').hidden = true;
    document.getElementById('newChatPanel').hidden = false;
    document.getElementById('newChatPhoneInput').value = '';
    document.getElementById('newChatError').textContent = '';
});

document.getElementById('newChatCancelBtn').addEventListener('click', () => {
    document.getElementById('newChatPanel').hidden = true;
});

document.getElementById('newChatSubmitBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const phoneInput = document.getElementById('newChatPhoneInput');
    const phone = phoneInput.value.trim();
    const errorEl = document.getElementById('newChatError');
    errorEl.textContent = '';
    if (!phone) {
        showError(errorEl, 'Enter a phone number.', phoneInput);
        return;
    }

    setLoading(btn, true);
    try {
        const lookupResp = await apiFetch(`/users/lookup?phone_number=${encodeURIComponent(phone)}`);
        const lookupData = await lookupResp.json().catch(() => ({}));
        if (!lookupResp.ok) {
            showError(errorEl, lookupData.detail || 'User not found.', phoneInput);
            return;
        }
        if (lookupData.id === session.user.id) {
            showError(errorEl, "That's your own number.", phoneInput);
            return;
        }

        const dmResp = await apiJson('/conversations/dm', 'POST', { other_user_id: lookupData.id });
        const dmData = await dmResp.json().catch(() => ({}));
        if (!dmResp.ok) {
            showError(errorEl, dmData.detail || 'Failed to start chat.');
            return;
        }

        document.getElementById('newChatPanel').hidden = true;
        await loadConversations();
        openConversation(dmData);
    } finally {
        setLoading(btn, false);
    }
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

document.getElementById('newGroupSubmitBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const nameInput = document.getElementById('newGroupNameInput');
    const name = nameInput.value.trim();
    const phonesRaw = document.getElementById('newGroupMembersInput').value.trim();
    const errorEl = document.getElementById('newGroupError');
    errorEl.textContent = '';
    if (!name) {
        showError(errorEl, 'Enter a group name.', nameInput);
        return;
    }

    setLoading(btn, true);
    try {
        const phones = phonesRaw
            .split(',')
            .map((p) => p.trim())
            .filter(Boolean);
        const memberIds = [];
        for (const phone of phones) {
            const lookupResp = await apiFetch(`/users/lookup?phone_number=${encodeURIComponent(phone)}`);
            if (!lookupResp.ok) {
                showError(errorEl, `Could not find a user with phone ${phone}.`);
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
            showError(errorEl, data.detail || 'Failed to create group.');
            return;
        }

        document.getElementById('newGroupPanel').hidden = true;
        await loadConversations();
        openConversation(data);
    } finally {
        setLoading(btn, false);
    }
});

// --- conversation view ---

async function openConversation(conv) {
    currentConversation = conv;
    oldestLoadedMessageId = null;
    hasMoreOlder = false;
    document.getElementById('chatTitle').textContent = conversationDisplayName(conv);
    const avatarInfo = conversationAvatarInfo(conv);
    const chatAvatar = document.getElementById('chatAvatar');
    renderAvatar(chatAvatar, avatarInfo.mediaId, avatarInfo.label);
    // Group photos are editable from here (the server enforces that only
    // admins can actually change it); a DM's avatar is just the other
    // person's profile photo, so it's not clickable.
    chatAvatar.classList.toggle('avatar-editable', conv.type === 'group');
    chatAvatar.title = conv.type === 'group' ? 'Change group photo' : '';
    document.getElementById('chatError').textContent = '';

    document.getElementById('chatEmpty').hidden = true;
    document.getElementById('chatArea').hidden = false;
    // Mobile: swap the stacked panes over to the chat.
    document.getElementById('appShell').classList.add('chat-open');
    renderConversationList(); // move the active highlight

    renderSkeletonRows(document.getElementById('messages'), 5);
    await loadMessages();
}

document.getElementById('chatAvatar').addEventListener('click', () => {
    if (currentConversation && currentConversation.type === 'group') {
        document.getElementById('groupAvatarInput').click();
    }
});

document.getElementById('groupAvatarInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file || !currentConversation) return;

    const errorEl = document.getElementById('chatError');
    errorEl.textContent = '';

    const formData = new FormData();
    formData.append('file', file);

    const resp = await apiFetch(`/conversations/${currentConversation.id}/avatar`, {
        method: 'POST',
        body: formData,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        showError(errorEl, data.detail || 'Failed to update group photo.');
        return;
    }

    currentConversation = data;
    const idx = conversations.findIndex((c) => c.id === data.id);
    if (idx !== -1) conversations[idx] = data;
    const avatarInfo = conversationAvatarInfo(data);
    renderAvatar(document.getElementById('chatAvatar'), avatarInfo.mediaId, avatarInfo.label);
});

// Mobile-only control: slides back from the chat pane to the list. The
// conversation stays "current" so incoming messages keep appending.
document.getElementById('backToInboxBtn').addEventListener('click', () => {
    document.getElementById('appShell').classList.remove('chat-open');
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
        list.innerHTML = ''; // clear the loading skeleton
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
    } else if (msg.type === 'call') {
        bubble.appendChild(buildCallMessageElement(msg));
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
    // Static, app-controlled markup (no user content interpolated here) --
    // safe to build via innerHTML, unlike message bodies/names elsewhere.
    link.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg> Download attachment';
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

    const attachBtn = document.getElementById('attachBtn');
    const errorEl = document.getElementById('chatError');
    errorEl.textContent = '';

    const formData = new FormData();
    formData.append('file', file);

    setLoading(attachBtn, true);
    try {
        const resp = await apiFetch('/media/upload', { method: 'POST', body: formData });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showError(errorEl, data.detail || 'Upload failed.');
            return;
        }

        if (!ws || ws.readyState !== WebSocket.OPEN) {
            showError(errorEl, 'Not connected, try again.');
            return;
        }
        ws.send(JSON.stringify({ conversation_id: currentConversation.id, media_id: data.id }));
    } finally {
        setLoading(attachBtn, false);
    }
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
