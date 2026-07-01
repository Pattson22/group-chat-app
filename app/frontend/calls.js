// Audio/video calling: mesh WebRTC signaled over the existing WS
// connection (see app.js's ws.onmessage -> handleCallEvent hook).
// No TURN server configured -- public STUN only, so calls across
// restrictive NATs may fail to connect. That's a known, accepted
// limitation for v1, not a bug.

const ICE_SERVERS = [{ urls: 'stun:stun.l.google.com:19302' }];

let activeCall = null; // { callId, conversationId, video, direction, participants: Set<user_id> }
let localStream = null;
let muted = false;
let cameraOff = false;
const peerConnections = new Map(); // user_id -> RTCPeerConnection
const pendingCandidates = new Map(); // user_id -> RTCIceCandidateInit[]

function mediaConstraints(video) {
    const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    return video
        ? { audio, video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 24 } } }
        : { audio, video: false };
}

function wsSendCall(action, extra) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ action, ...extra }));
}

function resolveMemberName(conversationId, userId) {
    const conv = conversations.find((c) => c.id === conversationId);
    if (!conv) return 'Unknown';
    const member = conv.members.find((m) => m.user_id === userId);
    return member ? member.display_name || member.phone_number : 'Unknown';
}

function resolveMemberAvatar(conversationId, userId) {
    const conv = conversations.find((c) => c.id === conversationId);
    if (!conv) return null;
    const member = conv.members.find((m) => m.user_id === userId);
    return member ? member.avatar_media_id : null;
}

// --- dispatch ---

function handleCallEvent(data) {
    switch (data.action) {
        case 'call:incoming':
            return onCallIncoming(data);
        case 'call:invited':
            return onCallInvited(data);
        case 'call:joined':
            return onCallJoined(data);
        case 'call:participant-joined':
            return onParticipantJoined(data);
        case 'call:participant-left':
            return onParticipantLeft(data);
        case 'call:declined':
            return onCallDeclined(data);
        case 'call:cancelled':
            return onCallCancelled(data);
        case 'call:ended':
            return onCallEnded(data);
        case 'call:error':
            return onCallError(data);
        case 'call:offer':
            return onRemoteOffer(data);
        case 'call:answer':
            return onRemoteAnswer(data);
        case 'call:ice-candidate':
            return onRemoteIceCandidate(data);
    }
}

// --- placing / receiving ---

async function startCall(video) {
    if (!currentConversation || activeCall) return;
    const conversationId = currentConversation.id;

    activeCall = { callId: null, conversationId, video, direction: 'outgoing', participants: new Set() };
    const peerAvatar = conversationAvatarInfo(currentConversation);
    showCallOverlayRinging('outgoing', peerAvatar.label, video, peerAvatar.mediaId);

    try {
        localStream = await navigator.mediaDevices.getUserMedia(mediaConstraints(video));
    } catch (e) {
        endCallLocally('Could not access camera/microphone.');
        return;
    }

    wsSendCall('call:invite', { conversation_id: conversationId, video });
}

document.getElementById('audioCallBtn').addEventListener('click', () => startCall(false));
document.getElementById('videoCallBtn').addEventListener('click', () => startCall(true));

function onCallInvited(data) {
    if (!activeCall || activeCall.conversationId !== data.conversation_id) return;
    activeCall.callId = data.call_id;

    if (data.rung_user_ids.length === 0) {
        endCallLocally('No one available to call.');
        return;
    }
    updateRingingStatus(`Ringing ${data.rung_user_ids.length} ${data.rung_user_ids.length === 1 ? 'person' : 'people'}...`);
}

function onCallIncoming(data) {
    if (activeCall) {
        // Already placing/in a call -- this simple UI only handles one at a time.
        wsSendCall('call:decline', { call_id: data.call_id });
        return;
    }
    activeCall = {
        callId: data.call_id,
        conversationId: data.conversation_id,
        video: data.video,
        direction: 'incoming',
        participants: new Set(),
    };
    const callerName = resolveMemberName(data.conversation_id, data.caller_id);
    const callerAvatar = resolveMemberAvatar(data.conversation_id, data.caller_id);
    showCallOverlayRinging('incoming', callerName, data.video, callerAvatar);
}

document.getElementById('callAcceptBtn').addEventListener('click', async () => {
    if (!activeCall) return;
    try {
        localStream = await navigator.mediaDevices.getUserMedia(mediaConstraints(activeCall.video));
    } catch (e) {
        wsSendCall('call:decline', { call_id: activeCall.callId });
        endCallLocally('Could not access camera/microphone.');
        return;
    }
    wsSendCall('call:accept', { call_id: activeCall.callId });
});

document.getElementById('callDeclineBtn').addEventListener('click', () => {
    if (!activeCall) return;
    wsSendCall('call:decline', { call_id: activeCall.callId });
    endCallLocally();
});

document.getElementById('callCancelBtn').addEventListener('click', () => {
    if (!activeCall) return;
    if (activeCall.callId) wsSendCall('call:leave', { call_id: activeCall.callId });
    endCallLocally();
});

document.getElementById('callHangupBtn').addEventListener('click', () => {
    if (!activeCall) return;
    wsSendCall('call:leave', { call_id: activeCall.callId });
    endCallLocally();
});

document.getElementById('callMuteBtn').addEventListener('click', () => {
    if (!localStream) return;
    muted = !muted;
    localStream.getAudioTracks().forEach((t) => (t.enabled = !muted));
    document.getElementById('callMuteBtn').classList.toggle('active', muted);
});

document.getElementById('callCameraBtn').addEventListener('click', () => {
    if (!localStream) return;
    cameraOff = !cameraOff;
    localStream.getVideoTracks().forEach((t) => (t.enabled = !cameraOff));
    document.getElementById('callCameraBtn').classList.toggle('active', cameraOff);
});

// --- join lifecycle ---

async function onCallJoined(data) {
    if (!activeCall) return;
    activeCall.callId = data.call_id;
    activeCall.video = data.video;

    if (!localStream) {
        try {
            localStream = await navigator.mediaDevices.getUserMedia(mediaConstraints(data.video));
        } catch (e) {
            wsSendCall('call:leave', { call_id: activeCall.callId });
            endCallLocally('Could not access camera/microphone.');
            return;
        }
    }
    enterActiveCallView();

    // Join-ordering convention: the newly-joined participant always offers
    // to everyone already there; existing participants only ever answer.
    for (const participantId of data.participants) {
        activeCall.participants.add(participantId);
        await createPeerConnectionAndOffer(participantId);
    }
}

function enterActiveCallView() {
    showCallOverlayActive();
    if (!document.getElementById('call-tile-local')) {
        renderLocalTile();
    }
}

function onParticipantJoined(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    const wasEmpty = activeCall.participants.size === 0;
    activeCall.participants.add(data.user_id);
    if (wasEmpty && activeCall.direction === 'outgoing') {
        // We (the caller) were still showing the ringing view -- the first
        // participant joining means the call is actually connecting now.
        enterActiveCallView();
    }
    ensureTilePlaceholder(data.user_id);
}

function onParticipantLeft(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    activeCall.participants.delete(data.user_id);
    removePeer(data.user_id);

    if (activeCall.participants.size === 0) {
        // Everyone else has left -- nothing left to stay connected to.
        wsSendCall('call:leave', { call_id: activeCall.callId });
        endCallLocally('Call ended.');
    }
}

function onCallDeclined(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    if (activeCall.direction === 'outgoing' && activeCall.participants.size === 0) {
        updateRingingStatus(`${resolveMemberName(activeCall.conversationId, data.user_id)} declined.`);
    }
}

function onCallCancelled(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    endCallLocally('Call ended.');
}

function onCallEnded(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    endCallLocally(data.outcome === 'missed' ? 'No answer.' : 'Call ended.');
}

function onCallError(data) {
    if (activeCall && (data.call_id === null || data.call_id === activeCall.callId)) {
        endCallLocally(`Call error: ${data.reason.replace(/_/g, ' ')}`);
    }
}

// --- WebRTC mechanics ---

function getOrCreatePeerConnection(userId) {
    let pc = peerConnections.get(userId);
    if (pc) return pc;

    pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    peerConnections.set(userId, pc);

    if (localStream) {
        localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));
    }

    pc.onicecandidate = (event) => {
        if (event.candidate) {
            wsSendCall('call:ice-candidate', {
                call_id: activeCall.callId,
                target_user_id: userId,
                candidate: event.candidate.toJSON(),
            });
        }
    };

    pc.ontrack = (event) => {
        attachRemoteStream(userId, event.streams[0]);
    };

    let disconnectTimer = null;
    pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
            removePeer(userId);
        } else if (pc.connectionState === 'disconnected') {
            // Browsers often self-recover from "disconnected" -- give it a
            // few seconds before treating the peer as actually gone. This
            // is purely local UI cleanup; it never notifies the server.
            disconnectTimer = setTimeout(() => {
                if (pc.connectionState === 'disconnected') removePeer(userId);
            }, 8000);
        } else if (pc.connectionState === 'connected' && disconnectTimer) {
            clearTimeout(disconnectTimer);
            disconnectTimer = null;
        }
    };

    return pc;
}

async function createPeerConnectionAndOffer(userId) {
    // Show a placeholder immediately rather than waiting for their media
    // track to arrive via ontrack -- otherwise a joiner sees nothing for
    // existing participants until (if ever) real media starts flowing.
    ensureTilePlaceholder(userId);
    const pc = getOrCreatePeerConnection(userId);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    wsSendCall('call:offer', { call_id: activeCall.callId, target_user_id: userId, sdp: pc.localDescription });
}

async function onRemoteOffer(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    const pc = getOrCreatePeerConnection(data.from_user_id);
    await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
    await flushPendingCandidates(data.from_user_id, pc);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    wsSendCall('call:answer', { call_id: activeCall.callId, target_user_id: data.from_user_id, sdp: pc.localDescription });
    activeCall.participants.add(data.from_user_id);
    ensureTilePlaceholder(data.from_user_id);
}

async function onRemoteAnswer(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    const pc = peerConnections.get(data.from_user_id);
    if (!pc) return;
    await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
    await flushPendingCandidates(data.from_user_id, pc);
}

async function onRemoteIceCandidate(data) {
    if (!activeCall || data.call_id !== activeCall.callId) return;
    const pc = peerConnections.get(data.from_user_id);
    if (!pc || !pc.remoteDescription) {
        if (!pendingCandidates.has(data.from_user_id)) pendingCandidates.set(data.from_user_id, []);
        pendingCandidates.get(data.from_user_id).push(data.candidate);
        return;
    }
    try {
        await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
    } catch (e) {
        // a late/duplicate candidate isn't fatal
    }
}

async function flushPendingCandidates(userId, pc) {
    const queued = pendingCandidates.get(userId);
    if (!queued) return;
    pendingCandidates.delete(userId);
    for (const candidate of queued) {
        try {
            await pc.addIceCandidate(new RTCIceCandidate(candidate));
        } catch (e) {
            // ignore
        }
    }
}

function removePeer(userId) {
    const pc = peerConnections.get(userId);
    if (pc) {
        pc.close();
        peerConnections.delete(userId);
    }
    pendingCandidates.delete(userId);
    const tile = document.getElementById(`call-tile-${userId}`);
    if (tile) tile.remove();
}

// --- overlay / tile rendering ---

function setCallButtonsEnabled(enabled) {
    const audioBtn = document.getElementById('audioCallBtn');
    const videoBtn = document.getElementById('videoCallBtn');
    if (audioBtn) audioBtn.disabled = !enabled;
    if (videoBtn) videoBtn.disabled = !enabled;
}

function showCallOverlayRinging(direction, peerName, video, peerAvatarMediaId) {
    setCallButtonsEnabled(false);
    document.getElementById('callOverlay').hidden = false;
    document.getElementById('callRingingView').hidden = false;
    document.getElementById('callActiveView').hidden = true;

    renderAvatar(document.getElementById('callAvatar'), peerAvatarMediaId, peerName);
    document.getElementById('callPeerName').textContent = peerName;
    document.getElementById('callStatusText').textContent =
        direction === 'outgoing' ? 'Calling...' : `Incoming ${video ? 'video' : 'audio'} call`;

    document.getElementById('callAcceptBtn').hidden = direction !== 'incoming';
    document.getElementById('callDeclineBtn').hidden = direction !== 'incoming';
    document.getElementById('callCancelBtn').hidden = direction !== 'outgoing';
}

function updateRingingStatus(text) {
    const el = document.getElementById('callStatusText');
    if (el) el.textContent = text;
}

function showCallOverlayActive() {
    document.getElementById('callRingingView').hidden = true;
    document.getElementById('callActiveView').hidden = false;
    document.getElementById('callCameraBtn').hidden = !activeCall.video;
    document.getElementById('callGrid').innerHTML = '';
    document.getElementById('callOverlay').hidden = false;
}

function hideCallOverlay() {
    document.getElementById('callOverlay').hidden = true;
    document.getElementById('callGrid').innerHTML = '';
    document.getElementById('callMuteBtn').classList.remove('active');
    document.getElementById('callCameraBtn').classList.remove('active');
    setCallButtonsEnabled(true);
}

function renderLocalTile() {
    const grid = document.getElementById('callGrid');
    const tile = document.createElement('div');
    tile.className = 'call-tile';
    tile.id = 'call-tile-local';

    if (activeCall.video && localStream) {
        const video = document.createElement('video');
        video.autoplay = true;
        video.muted = true;
        video.playsInline = true;
        video.srcObject = localStream;
        tile.appendChild(video);
    } else {
        const avatar = document.createElement('div');
        avatar.className = 'avatar call-tile-avatar';
        renderAvatar(avatar, session.user.avatar_media_id, session.user.display_name || session.user.phone_number);
        tile.appendChild(avatar);
    }

    const label = document.createElement('div');
    label.className = 'call-tile-label';
    label.textContent = 'You';
    tile.appendChild(label);

    grid.appendChild(tile);
}

function ensureTilePlaceholder(userId) {
    if (document.getElementById(`call-tile-${userId}`)) return;
    const grid = document.getElementById('callGrid');
    const tile = document.createElement('div');
    tile.className = 'call-tile';
    tile.id = `call-tile-${userId}`;

    const name = resolveMemberName(activeCall.conversationId, userId);
    const avatar = document.createElement('div');
    avatar.className = 'avatar call-tile-avatar';
    renderAvatar(avatar, resolveMemberAvatar(activeCall.conversationId, userId), name);
    tile.appendChild(avatar);

    const label = document.createElement('div');
    label.className = 'call-tile-label';
    label.textContent = name;
    tile.appendChild(label);

    grid.appendChild(tile);
}

function attachRemoteStream(userId, stream) {
    ensureTilePlaceholder(userId);
    const tile = document.getElementById(`call-tile-${userId}`);
    if (!tile || tile.querySelector('video')) return;

    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.srcObject = stream;
    tile.insertBefore(video, tile.firstChild);
    const avatar = tile.querySelector('.call-tile-avatar');
    if (avatar) avatar.remove();
}

// --- teardown ---

function endCallLocally(message) {
    const conversationId = activeCall ? activeCall.conversationId : null;

    for (const userId of Array.from(peerConnections.keys())) {
        removePeer(userId);
    }
    if (localStream) {
        localStream.getTracks().forEach((t) => t.stop());
        localStream = null;
    }
    muted = false;
    cameraOff = false;
    activeCall = null;
    hideCallOverlay();

    if (message && currentConversation && currentConversation.id === conversationId) {
        appendSystemNotice(message);
    }
}

// --- call-log message rendering (msg.type === 'call') ---

function formatDuration(totalSeconds) {
    const seconds = Math.max(0, totalSeconds || 0);
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
}

function buildCallMessageElement(msg) {
    const row = document.createElement('div');
    row.className = 'msg-call';

    const isVideo = !!msg.call_video;
    // Static SVG only, no user content -- safe to build via innerHTML.
    row.innerHTML = isVideo
        ? '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"></polygon><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>'
        : '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>';

    const labelSpan = document.createElement('span');
    if (msg.call_outcome === 'missed') {
        labelSpan.textContent = isVideo ? 'Missed video call' : 'Missed call';
    } else {
        labelSpan.textContent = `${isVideo ? 'Video' : 'Voice'} call · ${formatDuration(msg.call_duration_seconds)}`;
    }
    row.appendChild(labelSpan);

    return row;
}
