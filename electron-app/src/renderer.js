// OpenBell Dashboard — Renderer
// Connects to Rust server via WebSocket

const SERVER_URL = 'ws://localhost:5000/ws';
let ws = null;
let callState = 'idle';
let reconnectTimer = null;
let streamUrl = null;
let intercomActive = false;
let phoneAudioMuted = false;
let cvEnabled = true;

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
const btnCv = document.getElementById('btnCv');
const cvLabel = document.getElementById('cvLabel');

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
    case 'cv_state':
      updateCvState(msg.enabled);
      break;
    case 'person_detected':
      onPersonDetected(msg);
      break;
    case 'person_left':
      onPersonLeft(msg);
      break;
    case 'assistant_activate':
      onAssistantActivate(msg);
      break;
    case 'assistant_deactivate':
      onAssistantDeactivate(msg);
      break;
    default:
      console.log('Unknown message:', msg);
  }
}

function updateStatus(msg) {
  if (msg.stream_url) {
    // Only reset the feed when the stream URL actually changes.
    // Resetting on every status broadcast kills the live MJPEG connection.
    if (msg.stream_url !== streamUrl) {
      streamUrl = msg.stream_url;
      setVideoFeed(streamUrl);
    } else if (!streamUrl) {
      streamUrl = msg.stream_url;
      setVideoFeed(streamUrl);
    }
  }
  if (msg.call_state) {
    updateCallState(msg.call_state);
  }
  if (msg.phone_audio_muted !== undefined) {
    updatePhoneAudioMute(msg.phone_audio_muted);
  }
  if (msg.cv_enabled !== undefined) {
    updateCvState(msg.cv_enabled);
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

function onPersonDetected(msg) {
  addEvent('person_detected', msg.timestamp);
  console.log(
    `CV: Person detected — ${msg.person_count} person(s), ` +
    `conf=${msg.max_confidence?.toFixed(2)}, snapshot=${msg.snapshot_file}`
  );
  // Show a notification if not already in a call
  if (callState === 'idle' && Notification.permission === 'granted') {
    new Notification('OpenBell — Person Detected', {
      body: `${msg.person_count} person(s) spotted at the door`,
    });
  }
}

function onPersonLeft(msg) {
  addEvent('person_left', msg.timestamp);
  console.log('CV: Person left frame');
}

function onAssistantActivate(msg) {
  addEvent('assistant_active', msg.timestamp);
  callBanner.className = 'call-banner answered';
  callBannerText.textContent = '🤖 VOICE ASSISTANT ACTIVE';
  callActions.innerHTML = `
    <button class="btn btn-hangup" onclick="endCall()">■ End Session</button>
  `;
  document.title = '[🤖] Assistant Active';
  try { ringSound.pause(); ringSound.currentTime = 0; } catch(e) {}
  console.log('Voice assistant activated');
}

function onAssistantDeactivate(msg) {
  addEvent('assistant_ended', msg.timestamp);
  console.log('Voice assistant session ended');
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

function toggleCv() {
  const newEnabled = !cvEnabled;
  send({ type: 'toggle_cv', enabled: newEnabled });
}

function updateCvState(enabled) {
  cvEnabled = enabled;
  if (btnCv) {
    if (enabled) {
      btnCv.classList.remove('disabled');
      btnCv.textContent = '◉ CV On';
    } else {
      btnCv.classList.add('disabled');
      btnCv.textContent = '▪ CV Off';
    }
  }
  if (cvLabel) {
    cvLabel.textContent = enabled ? 'person detection active' : 'person detection paused';
  }
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
let feedCheckTimer = null;
let feedSetAt = 0;          // timestamp when we last set a new feed URL
let feedRetryPending = false;
let currentFeedUrl = '';     // the actual src we last applied (with cache-bust)

function setVideoFeed(url) {
  if (!url) return;

  // Cache-bust to force a fresh MJPEG connection
  const bustUrl = url + (url.includes('?') ? '&' : '?') + '_t=' + Date.now();
  currentFeedUrl = bustUrl;
  feedSetAt = Date.now();
  feedRetryPending = false;
  videoFeed.src = bustUrl;
  videoFeed.style.display = 'block';
  noFeed.style.display = 'none';
  console.log('Video feed set:', url);

  videoFeed.onerror = () => {
    console.warn('Video feed error — will retry');
    scheduleVideoRetry();
  };

  // Periodic health check: if the img naturalWidth is 0 long after we
  // started loading, the MJPEG connection has silently died.  Reconnect.
  // Grace period: don't check for the first 12 seconds after setting a
  // new source — the connection needs time to establish and deliver a frame.
  clearInterval(feedCheckTimer);
  feedCheckTimer = setInterval(() => {
    if (!streamUrl || videoFeed.style.display !== 'block') return;
    const elapsed = Date.now() - feedSetAt;
    if (elapsed < 12000) return;  // grace period — still loading
    if (videoFeed.naturalWidth === 0) {
      console.warn('Video feed stalled (no frames for ' + Math.round(elapsed/1000) + 's) — reconnecting');
      scheduleVideoRetry();
    }
  }, 8000);
}

function scheduleVideoRetry() {
  if (feedRetryPending) return;  // don't stack retries
  feedRetryPending = true;
  videoFeed.style.display = 'none';
  noFeed.style.display = 'flex';
  noFeed.textContent = 'Camera feed lost — reconnecting...';
  // Remove old src to close the HTTP connection
  videoFeed.src = '';
  setTimeout(() => {
    feedRetryPending = false;
    if (streamUrl) setVideoFeed(streamUrl);
  }, 3000);
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
    assistant_active: '🤖 Assistant Active',
    assistant_ended: '🤖 Assistant Done',
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

// ═══════════════════════════════════════════════════════════
//  PANEL LAYOUT ENGINE — Drag, Resize, Lock/Unlock
// ═══════════════════════════════════════════════════════════

const LAYOUT_KEY = 'openbell_panel_layout';
const canvas = document.getElementById('layoutCanvas');
const btnLock = document.getElementById('btnLock');
let layoutLocked = true;

// Default layout (percentages of canvas size)
const DEFAULT_LAYOUT = {
  camera:  { x: 1,  y: 1,  w: 64, h: 98 },
  events:  { x: 66, y: 1,  w: 33, h: 54 },
  history: { x: 66, y: 56, w: 33, h: 43 },
};

function getCanvasRect() {
  return canvas.getBoundingClientRect();
}

// Convert percent layout → pixel style
function applyLayout(layout) {
  const rect = getCanvasRect();
  document.querySelectorAll('.panel').forEach(panel => {
    const id = panel.dataset.panel;
    const pos = layout[id];
    if (!pos) return;
    panel.style.left   = (pos.x / 100 * rect.width)  + 'px';
    panel.style.top    = (pos.y / 100 * rect.height) + 'px';
    panel.style.width  = (pos.w / 100 * rect.width)  + 'px';
    panel.style.height = (pos.h / 100 * rect.height) + 'px';
  });
}

// Read current pixel positions → percent layout
function readLayout() {
  const rect = getCanvasRect();
  const layout = {};
  document.querySelectorAll('.panel').forEach(panel => {
    const id = panel.dataset.panel;
    layout[id] = {
      x: parseFloat(panel.style.left) / rect.width  * 100,
      y: parseFloat(panel.style.top)  / rect.height * 100,
      w: parseFloat(panel.style.width) / rect.width  * 100,
      h: parseFloat(panel.style.height) / rect.height * 100,
    };
  });
  return layout;
}

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(readLayout()));
  } catch (e) { /* ignore */ }
}

function loadLayout() {
  try {
    const saved = localStorage.getItem(LAYOUT_KEY);
    if (saved) return JSON.parse(saved);
  } catch (e) { /* ignore */ }
  return null;
}

function resetLayout() {
  localStorage.removeItem(LAYOUT_KEY);
  applyLayout(DEFAULT_LAYOUT);
}

// ── Lock / Unlock Toggle ──
function toggleLayoutLock() {
  layoutLocked = !layoutLocked;
  canvas.classList.toggle('unlocked', !layoutLocked);
  btnLock.classList.toggle('unlocked', !layoutLocked);
  btnLock.textContent = layoutLocked ? '🔒 Locked' : '🔓 Edit Layout';
  if (layoutLocked) {
    saveLayout();
  }
}
// Make globally accessible for onclick
window.toggleLayoutLock = toggleLayoutLock;

// ── Drag Logic ──
let dragPanel = null, dragStartX = 0, dragStartY = 0, dragOrigLeft = 0, dragOrigTop = 0;

function onDragStart(e) {
  if (layoutLocked) return;
  // Only start drag from handle
  const handle = e.target.closest('.panel-drag-handle');
  if (!handle) return;
  const panel = handle.closest('.panel');
  if (!panel) return;

  e.preventDefault();
  dragPanel = panel;
  dragPanel.classList.add('dragging');

  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches ? e.touches[0].clientY : e.clientY;
  dragStartX = clientX;
  dragStartY = clientY;
  dragOrigLeft = parseFloat(panel.style.left) || 0;
  dragOrigTop  = parseFloat(panel.style.top)  || 0;

  document.addEventListener('mousemove', onDragMove, { passive: false });
  document.addEventListener('mouseup', onDragEnd);
  document.addEventListener('touchmove', onDragMove, { passive: false });
  document.addEventListener('touchend', onDragEnd);
}

function onDragMove(e) {
  if (!dragPanel) return;
  e.preventDefault();
  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches ? e.touches[0].clientY : e.clientY;
  const dx = clientX - dragStartX;
  const dy = clientY - dragStartY;

  const rect = getCanvasRect();
  let newLeft = dragOrigLeft + dx;
  let newTop  = dragOrigTop  + dy;

  // Clamp to canvas bounds
  const pw = parseFloat(dragPanel.style.width) || 100;
  const ph = parseFloat(dragPanel.style.height) || 100;
  newLeft = Math.max(0, Math.min(newLeft, rect.width - pw));
  newTop  = Math.max(0, Math.min(newTop, rect.height - ph));

  dragPanel.style.left = newLeft + 'px';
  dragPanel.style.top  = newTop  + 'px';
}

function onDragEnd() {
  if (dragPanel) {
    dragPanel.classList.remove('dragging');
    dragPanel = null;
  }
  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('mouseup', onDragEnd);
  document.removeEventListener('touchmove', onDragMove);
  document.removeEventListener('touchend', onDragEnd);
}

// ── Resize Logic ──
let resizePanel = null, resizeStartX = 0, resizeStartY = 0, resizeOrigW = 0, resizeOrigH = 0;

function onResizeStart(e) {
  if (layoutLocked) return;
  const handle = e.target.closest('.panel-resize-handle');
  if (!handle) return;
  const panel = handle.closest('.panel');
  if (!panel) return;

  e.preventDefault();
  e.stopPropagation();
  resizePanel = panel;
  resizePanel.classList.add('resizing');

  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches ? e.touches[0].clientY : e.clientY;
  resizeStartX = clientX;
  resizeStartY = clientY;
  resizeOrigW = parseFloat(panel.style.width)  || 200;
  resizeOrigH = parseFloat(panel.style.height) || 200;

  document.addEventListener('mousemove', onResizeMove, { passive: false });
  document.addEventListener('mouseup', onResizeEnd);
  document.addEventListener('touchmove', onResizeMove, { passive: false });
  document.addEventListener('touchend', onResizeEnd);
}

function onResizeMove(e) {
  if (!resizePanel) return;
  e.preventDefault();
  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches ? e.touches[0].clientY : e.clientY;
  const dx = clientX - resizeStartX;
  const dy = clientY - resizeStartY;

  const rect = getCanvasRect();
  const left = parseFloat(resizePanel.style.left) || 0;
  const top  = parseFloat(resizePanel.style.top)  || 0;

  // Min sizes
  let newW = Math.max(150, resizeOrigW + dx);
  let newH = Math.max(100, resizeOrigH + dy);

  // Clamp to canvas bounds
  newW = Math.min(newW, rect.width - left);
  newH = Math.min(newH, rect.height - top);

  resizePanel.style.width  = newW + 'px';
  resizePanel.style.height = newH + 'px';
}

function onResizeEnd() {
  if (resizePanel) {
    resizePanel.classList.remove('resizing');
    resizePanel = null;
  }
  document.removeEventListener('mousemove', onResizeMove);
  document.removeEventListener('mouseup', onResizeEnd);
  document.removeEventListener('touchmove', onResizeMove);
  document.removeEventListener('touchend', onResizeEnd);
}

// ── Event Listeners ──
canvas.addEventListener('mousedown', (e) => {
  if (e.target.closest('.panel-resize-handle')) {
    onResizeStart(e);
  } else {
    onDragStart(e);
  }
});
canvas.addEventListener('touchstart', (e) => {
  if (e.target.closest('.panel-resize-handle')) {
    onResizeStart(e);
  } else {
    onDragStart(e);
  }
}, { passive: false });

// ── Reflow on window resize (maintain percentages) ──
let reflowTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(reflowTimer);
  reflowTimer = setTimeout(() => {
    const layout = loadLayout() || DEFAULT_LAYOUT;
    applyLayout(layout);
  }, 100);
});

// ── Double-click drag handle → reset that panel to defaults ──
canvas.addEventListener('dblclick', (e) => {
  if (layoutLocked) return;
  const handle = e.target.closest('.panel-drag-handle');
  if (!handle) return;
  const panel = handle.closest('.panel');
  if (!panel) return;
  const id = panel.dataset.panel;
  const rect = getCanvasRect();
  const def = DEFAULT_LAYOUT[id];
  if (def) {
    panel.style.left   = (def.x / 100 * rect.width)  + 'px';
    panel.style.top    = (def.y / 100 * rect.height) + 'px';
    panel.style.width  = (def.w / 100 * rect.width)  + 'px';
    panel.style.height = (def.h / 100 * rect.height) + 'px';
  }
});

// ── Apply saved or default layout on load ──
(function initLayout() {
  const saved = loadLayout();
  applyLayout(saved || DEFAULT_LAYOUT);
})();
