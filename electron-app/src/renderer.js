// OpenBell Dashboard — Renderer
// Connects to Rust server via WebSocket

const SERVER_URL = 'ws://localhost:5000/ws';
let ws = null;
let callState = 'idle';
let reconnectTimer = null;
let streamUrl = null;
let intercomActive = false;
let phoneAudioMuted = false;

// ── DOM refs ──
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const callBanner = document.getElementById('callBanner');
const callBannerText = document.getElementById('callBannerText');
const callActions = document.getElementById('callActions');
const eventList = document.getElementById('eventList');
const callHistory = document.getElementById('callHistory');
const videoFeed = document.getElementById('videoFeed');
const noFeed = document.getElementById('noFeed');
const ringSound = document.getElementById('ringSound');
const btnIntercom = document.getElementById('btnIntercom');
const intercomHint = document.getElementById('intercomHint');
const btnPhoneAudio = document.getElementById('btnPhoneAudio');
const phoneAudioLabel = document.getElementById('phoneAudioLabel');

// ── WebSocket Connection ──
function connect() {
  if (ws && ws.readyState <= 1) return;

  ws = new WebSocket(SERVER_URL);

  ws.onopen = () => {
    statusDot.className = 'status-dot connected';
    statusText.textContent = 'Connected';
    clearTimeout(reconnectTimer);
    console.log('WebSocket connected');
  };

  ws.onclose = () => {
    statusDot.className = 'status-dot';
    statusText.textContent = 'Disconnected';
    scheduleReconnect();
  };

  ws.onerror = () => {
    statusDot.className = 'status-dot';
    statusText.textContent = 'Connection error';
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleMessage(msg);
    } catch (e) {
      console.error('Bad message:', event.data);
    }
  };
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 2000);
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

// ── Message handlers ──
function handleMessage(msg) {
  switch (msg.type) {
    case 'status':
      updateStatus(msg);
      break;
    case 'call_state':
      updateCallState(msg.state, msg.call_id);
      break;
    case 'doorbell_press':
      onDoorbellPress(msg.timestamp);
      break;
    case 'start_audio':
      break;
    case 'stop_audio':
      break;
    case 'intercom_state':
      updateIntercomState(msg.active);
      break;
    case 'phone_audio_mute':
      updatePhoneAudioMute(msg.muted);
      break;
    default:
      console.log('Unknown message:', msg);
  }
}

function updateStatus(msg) {
  if (msg.stream_url && msg.stream_url !== streamUrl) {
    streamUrl = msg.stream_url;
    setVideoFeed(streamUrl);
  }
  if (msg.call_state) {
    updateCallState(msg.call_state);
  }
  if (msg.phone_audio_muted !== undefined) {
    updatePhoneAudioMute(msg.phone_audio_muted);
  }
}

function updateCallState(state, callId) {
  callState = state;

  switch (state) {
    case 'ringing':
      callBanner.className = 'call-banner ringing';
      callBannerText.textContent = '▶ DOORBELL RINGING ◀';
      callActions.innerHTML = `
        <button class="btn btn-answer" onclick="answerCall()">▶ Answer</button>
        <button class="btn btn-ignore" onclick="endCall()">✕ Ignore</button>
      `;
      document.title = '[!] DOORBELL';
      try { ringSound.play(); } catch(e) {}
      break;

    case 'answered':
      callBanner.className = 'call-banner answered';
      callBannerText.textContent = '● CALL ACTIVE';
      callActions.innerHTML = `
        <button class="btn btn-hangup" onclick="endCall()">■ Hang Up</button>
      `;
      document.title = '[>] In Call...';
      try { ringSound.pause(); ringSound.currentTime = 0; } catch(e) {}
      break;

    case 'idle':
    case 'ended':
      callBanner.className = 'call-banner';
      document.title = 'OpenBell';
      try { ringSound.pause(); ringSound.currentTime = 0; } catch(e) {}
      break;
  }
}

function onDoorbellPress(timestamp) {
  addEvent('doorbell_press', timestamp);

  // Show system notification
  if (Notification.permission === 'granted') {
    new Notification('OpenBell', { body: 'Someone is at the door!' });
  } else if (Notification.permission !== 'denied') {
    Notification.requestPermission();
  }
}

// ── Actions ──
function answerCall() {
  send({ type: 'answer_call' });
}

function endCall() {
  send({ type: 'end_call' });
}

function intercomDown() {
  if (!intercomActive) {
    send({ type: 'intercom_start' });
  }
}

function intercomUp() {
  if (intercomActive) {
    send({ type: 'intercom_stop' });
  }
}

function updateIntercomState(active) {
  intercomActive = active;
  if (active) {
    btnIntercom.classList.add('active');
    intercomHint.textContent = '● transmitting...';
  } else {
    btnIntercom.classList.remove('active');
    intercomHint.textContent = 'push-to-talk intercom';
  }
}

function togglePhoneAudio() {
  const newMuted = !phoneAudioMuted;
  send({ type: 'toggle_phone_audio', muted: newMuted });
}

function updatePhoneAudioMute(muted) {
  phoneAudioMuted = muted;
  if (btnPhoneAudio) {
    if (muted) {
      btnPhoneAudio.classList.add('muted');
      btnPhoneAudio.textContent = '▪ Audio Off';
    } else {
      btnPhoneAudio.classList.remove('muted');
      btnPhoneAudio.textContent = '▶ Audio On';
    }
  }
  if (phoneAudioLabel) {
    phoneAudioLabel.textContent = muted ? 'Phone mic → muted' : 'Phone mic → PC speakers';
  }
}

// ── Video feed ──
function setVideoFeed(url) {
  if (!url) return;
  videoFeed.src = url;
  videoFeed.style.display = 'block';
  noFeed.style.display = 'none';
  videoFeed.onerror = () => {
    videoFeed.style.display = 'none';
    noFeed.style.display = 'flex';
    noFeed.textContent = 'Camera feed lost';
    // Retry after a few seconds
    setTimeout(() => {
      if (streamUrl) {
        videoFeed.src = streamUrl;
        videoFeed.style.display = 'block';
        noFeed.style.display = 'none';
      }
    }, 3000);
  };
}

// ── Event log ──
function addEvent(type, timestamp) {
  const div = document.createElement('div');
  div.className = `event-item ${type}`;
  const time = timestamp
    ? new Date(timestamp * 1000).toLocaleTimeString()
    : new Date().toLocaleTimeString();
  const names = {
    doorbell_press: 'OPENBELL PRESSED',
    person_detected: 'Person Detected',
    person_left: 'Person Left',
    package_detected: 'Package Detected',
  };
  div.innerHTML = `
    <div class="ev-type">${names[type] || type}</div>
    <div class="ev-time">${time}</div>
  `;
  eventList.insertBefore(div, eventList.firstChild);

  // Keep max 100 events in DOM
  while (eventList.children.length > 100) {
    eventList.removeChild(eventList.lastChild);
  }
}

// ── Init ──
connect();
