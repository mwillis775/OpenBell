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
        <button class="btn btn-answer">▶ Answer</button>
        <button class="btn btn-ignore">✕ Ignore</button>
      `;
      document.title = '[!] DOORBELL';
      try { ringSound.play(); } catch(e) {}
      break;

    case 'answered':
      callBanner.className = 'call-banner answered';
      callBannerText.textContent = '● CALL ACTIVE';
      callActions.innerHTML = `
        <button class="btn btn-hangup">■ Hang Up</button>
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
  const names = msg.identities && msg.identities.length > 0
    ? msg.identities.filter(n => n !== 'unknown')
    : [];
  const label = names.length > 0
    ? names.join(', ')
    : `${msg.person_count} person(s)`;
  addEvent('person_detected', msg.timestamp, label);
  console.log(
    `CV: Person detected — ${msg.person_count} person(s), ` +
    `conf=${msg.max_confidence?.toFixed(2)}, snapshot=${msg.snapshot_file}` +
    (names.length > 0 ? `, identified: ${names.join(', ')}` : '')
  );
  // Show a notification if not already in a call
  if (callState === 'idle' && Notification.permission === 'granted') {
    new Notification('OpenBell — Person Detected', {
      body: names.length > 0
        ? `${label} at the door`
        : `${msg.person_count} person(s) spotted at the door`,
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
    <button class="btn btn-hangup">■ End Session</button>
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
function addEvent(type, timestamp, detail) {
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
  const label = names[type] || type;
  const detailHtml = detail ? `<div class="ev-detail">${detail}</div>` : '';
  div.innerHTML = `
    <div class="ev-type">${label}</div>
    ${detailHtml}
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

// Request notification permission on load
if ('Notification' in window && Notification.permission === 'default') {
  Notification.requestPermission();
}

// ═══════════════════════════════════════════════════════════
//  MEDIA GALLERY — Browse snapshots & recordings (via CV server)
// ═══════════════════════════════════════════════════════════

const CV_BASE = 'http://localhost:5100';
let galleryOpen = false;
let galleryRefreshTimer = null;
let galleryFilter = 'all'; // 'all', 'snapshot', 'recording'
let gallerySearchQuery = '';
let gallerySelectMode = false;
let gallerySelection = new Set(); // filenames
let galleryItems = []; // all fetched items
let galleryFiltered = []; // items after filter + search
let lightboxIndex = -1; // current index in galleryFiltered

const galleryOverlay = document.getElementById('galleryOverlay');
const galleryGrid = document.getElementById('galleryGrid');
const galleryBody = document.getElementById('galleryBody');
const galleryStatus = document.getElementById('galleryStatus');
const galleryStorage = document.getElementById('galleryStorage');
const galleryEmpty = document.getElementById('galleryEmpty');
const gallerySearchInput = document.getElementById('gallerySearch');
const lightbox = document.getElementById('lightbox');
const lightboxContent = document.getElementById('lightboxContent');
const lightboxInfo = document.getElementById('lightboxInfo');
const lightboxCounter = document.getElementById('lightboxCounter');
const deleteDialog = document.getElementById('deleteDialog');
const deleteDialogMsg = document.getElementById('deleteDialogMsg');
const btnDeleteSelected = document.getElementById('btnDeleteSelected');
const btnSelectMode = document.getElementById('btnSelectMode');

function openGallery() {
  galleryOverlay.classList.add('active');
  galleryOpen = true;
  gallerySearchInput.value = '';
  gallerySearchQuery = '';
  gallerySelection.clear();
  gallerySelectMode = false;
  updateSelectModeUI();
  refreshGallery();
  galleryRefreshTimer = setInterval(refreshGallery, 30000);
}

function closeGallery() {
  galleryOverlay.classList.remove('active');
  galleryOpen = false;
  clearInterval(galleryRefreshTimer);
  galleryRefreshTimer = null;
}

async function refreshGallery() {
  galleryStatus.textContent = '[ loading... ]';

  try {
    const resp = await fetch(CV_BASE + '/media/list', { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    galleryItems = data.media || [];

    if (data.storage) renderStorageStats(data.storage);

    applyFilters();
  } catch (e) {
    console.warn('Gallery fetch failed:', e.message);
    galleryStatus.textContent = '[ failed to load — CV server may be offline ]';
  }
}

function applyFilters() {
  const q = gallerySearchQuery.toLowerCase();
  galleryFiltered = galleryItems.filter(item => {
    if (galleryFilter !== 'all' && item.type !== galleryFilter) return false;
    if (q && !item.filename.toLowerCase().includes(q)) return false;
    return true;
  });

  const snapCount = galleryItems.filter(i => i.type === 'snapshot').length;
  const recCount = galleryItems.filter(i => i.type === 'recording').length;

  let statusParts = [];
  if (galleryFilter === 'all') {
    statusParts.push(`${galleryFiltered.length} item(s)`);
    if (snapCount > 0) statusParts.push(`${snapCount} snapshots`);
    if (recCount > 0) statusParts.push(`${recCount} recordings`);
  } else {
    statusParts.push(`${galleryFiltered.length} ${galleryFilter}(s)`);
  }
  if (q) statusParts.push(`matching "${gallerySearchQuery}"`);
  galleryStatus.textContent = `[ ${statusParts.join(' · ')} ]`;

  // Update tab counts
  document.querySelectorAll('.gallery-tab').forEach(tab => {
    const f = tab.dataset.filter;
    if (f === 'all') {
      tab.textContent = `All (${galleryItems.length})`;
    } else if (f === 'snapshot') {
      tab.textContent = `Snapshots (${snapCount})`;
    } else if (f === 'recording') {
      tab.textContent = `Recordings (${recCount})`;
    }
  });

  renderGallery(galleryFiltered);
}

function renderStorageStats(stats) {
  const snapMB = stats.snapshots_mb || 0;
  const recMB = stats.recordings_mb || 0;
  const snapMax = stats.snapshots_limit_mb || 100;
  const recMax = stats.recordings_limit_mb || 500;
  const snapPct = Math.min(100, (snapMB / snapMax) * 100);
  const recPct = Math.min(100, (recMB / recMax) * 100);

  function barClass(pct) {
    if (pct > 90) return 'critical';
    if (pct > 70) return 'warn';
    return '';
  }

  galleryStorage.innerHTML = `
    <span class="storage-item">
      snapshots: ${snapMB.toFixed(1)}/${snapMax} MB
      <span class="storage-bar"><span class="storage-bar-fill ${barClass(snapPct)}" style="width:${snapPct}%"></span></span>
    </span>
    <span class="storage-item">
      recordings: ${recMB.toFixed(1)}/${recMax} MB
      <span class="storage-bar"><span class="storage-bar-fill ${barClass(recPct)}" style="width:${recPct}%"></span></span>
    </span>
  `;
}

function renderGallery(items) {
  // Group by date
  const groups = new Map();
  for (const item of items) {
    const d = new Date(item.timestamp);
    const key = d.toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }

  // Clear existing content from parent (galleryBody contains both grid wrapper and empty state)
  // We rebuild the grid content inside galleryBody
  const bodyEl = galleryBody;
  // Remove old date groups
  bodyEl.querySelectorAll('.gallery-date-group').forEach(el => el.remove());
  galleryGrid.innerHTML = '';
  galleryGrid.style.display = 'none';

  if (items.length === 0) {
    galleryEmpty.style.display = 'flex';
    return;
  }

  galleryEmpty.style.display = 'none';

  for (const [dateLabel, dateItems] of groups) {
    const groupEl = document.createElement('div');
    groupEl.className = 'gallery-date-group';

    const headerEl = document.createElement('div');
    headerEl.className = 'gallery-date-header';
    headerEl.innerHTML = `
      <span>${dateLabel}</span>
      <span class="gallery-date-count">${dateItems.length} item(s)</span>
    `;
    groupEl.appendChild(headerEl);

    const gridEl = document.createElement('div');
    gridEl.className = 'gallery-grid';

    for (const item of dateItems) {
      const card = createGalleryCard(item);
      gridEl.appendChild(card);
    }

    groupEl.appendChild(gridEl);
    bodyEl.appendChild(groupEl);
  }
}

function createGalleryCard(item) {
  const card = document.createElement('div');
  const isRecording = item.type === 'recording';
  card.className = `gallery-card ${item.type}`;
  if (gallerySelection.has(item.filename)) card.classList.add('selected');
  card.dataset.filename = item.filename;
  card.dataset.type = item.type;

  const time = new Date(item.timestamp).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  const sizeLabel = item.size > 1048576
    ? (item.size / 1048576).toFixed(1) + ' MB'
    : (item.size / 1024).toFixed(0) + ' KB';

  const badge = isRecording ? '▶ recording' : '◉ snapshot';

  let thumbHtml;
  if (isRecording) {
    // Video thumbnail: use first frame via video element or show icon
    thumbHtml = `
      <div class="gallery-thumb">
        <div class="gallery-no-thumb">▶</div>
        <div class="gallery-play-icon"></div>
        <span class="gallery-type-badge">${badge}</span>
      </div>
    `;
  } else {
    const thumbUrl = `${CV_BASE}/snapshots/file/${encodeURIComponent(item.filename)}`;
    thumbHtml = `
      <div class="gallery-thumb">
        <img src="${thumbUrl}" alt="${item.filename}" loading="lazy">
        <span class="gallery-type-badge">${badge}</span>
      </div>
    `;
  }

  card.innerHTML = `
    ${thumbHtml}
    <button class="gallery-delete" title="Delete">✕</button>
    <div class="gallery-meta">
      <div class="gallery-date">${time}</div>
      <div class="gallery-size">${sizeLabel}</div>
    </div>
  `;

  return card;
}

// ── Lightbox ──
function openLightbox(index) {
  if (index < 0 || index >= galleryFiltered.length) return;
  lightboxIndex = index;
  const item = galleryFiltered[index];
  const isRecording = item.type === 'recording';
  const endpoint = isRecording ? 'recordings' : 'snapshots';
  const url = `${CV_BASE}/${endpoint}/file/${encodeURIComponent(item.filename)}`;

  lightbox.classList.add('active');

  if (isRecording) {
    lightboxContent.innerHTML = `
      <video class="lightbox-video" controls autoplay>
        <source src="${url}" type="video/mp4">
      </video>
    `;
  } else {
    lightboxContent.innerHTML = `<img src="${url}" class="lightbox-image" alt="${item.filename}">`;
  }

  const date = new Date(item.timestamp).toLocaleString();
  const sizeLabel = item.size > 1048576
    ? (item.size / 1048576).toFixed(1) + ' MB'
    : (item.size / 1024).toFixed(0) + ' KB';
  lightboxInfo.textContent = `${item.filename} · ${date} · ${sizeLabel}`;
  lightboxCounter.textContent = `${index + 1} / ${galleryFiltered.length}`;

  updateLightboxNav();
}

function updateLightboxNav() {
  document.getElementById('btnLightboxPrev').disabled = lightboxIndex <= 0;
  document.getElementById('btnLightboxNext').disabled = lightboxIndex >= galleryFiltered.length - 1;
}

function lightboxPrev() {
  if (lightboxIndex > 0) openLightbox(lightboxIndex - 1);
}

function lightboxNext() {
  if (lightboxIndex < galleryFiltered.length - 1) openLightbox(lightboxIndex + 1);
}

function closeLightbox() {
  lightbox.classList.remove('active');
  const video = lightboxContent.querySelector('video');
  if (video) video.pause();
  lightboxContent.innerHTML = '';
  lightboxIndex = -1;
}

function lightboxDownload() {
  if (lightboxIndex < 0 || lightboxIndex >= galleryFiltered.length) return;
  const item = galleryFiltered[lightboxIndex];
  const endpoint = item.type === 'recording' ? 'recordings' : 'snapshots';
  const url = `${CV_BASE}/${endpoint}/file/${encodeURIComponent(item.filename)}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = item.filename;
  a.click();
}

// ── Select mode ──
function toggleSelectMode() {
  gallerySelectMode = !gallerySelectMode;
  gallerySelection.clear();
  updateSelectModeUI();
  // Re-render to add/remove checkmarks
  applyFilters();
}

function updateSelectModeUI() {
  btnSelectMode.classList.toggle('active', gallerySelectMode);
  btnSelectMode.textContent = gallerySelectMode ? '☑ Selecting' : '☐ Select';
  btnDeleteSelected.style.display = gallerySelectMode && gallerySelection.size > 0 ? '' : 'none';
  if (gallerySelection.size > 0) {
    btnDeleteSelected.textContent = `✕ Delete (${gallerySelection.size})`;
  }
}

// ── Delete helpers ──
let pendingDeleteResolve = null;

function showDeleteConfirm(message) {
  return new Promise(resolve => {
    pendingDeleteResolve = resolve;
    deleteDialogMsg.textContent = message;
    deleteDialog.classList.add('active');
  });
}

function hideDeleteDialog() {
  deleteDialog.classList.remove('active');
  if (pendingDeleteResolve) {
    pendingDeleteResolve(false);
    pendingDeleteResolve = null;
  }
}

async function deleteMedia(filename, type) {
  const endpoint = type === 'recording' ? 'recordings' : 'snapshots';
  const resp = await fetch(`${CV_BASE}/${endpoint}/file/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
    signal: AbortSignal.timeout(5000),
  });
  return resp.ok;
}

async function deleteSingleItem(filename, type) {
  const confirmed = await showDeleteConfirm(`Are you sure you want to delete "${filename}"?`);
  if (!confirmed) return;
  try {
    const ok = await deleteMedia(filename, type);
    if (ok) {
      galleryItems = galleryItems.filter(i => i.filename !== filename);
      gallerySelection.delete(filename);
      // If in lightbox, move to next or close
      if (lightbox.classList.contains('active')) {
        if (galleryFiltered.length <= 1) {
          closeLightbox();
        } else if (lightboxIndex >= galleryFiltered.length - 1) {
          // was last item
        }
      }
      applyFilters();
      // Re-open lightbox at same position if still open
      if (lightbox.classList.contains('active') && galleryFiltered.length > 0) {
        const newIdx = Math.min(lightboxIndex, galleryFiltered.length - 1);
        openLightbox(newIdx);
      } else {
        closeLightbox();
      }
    }
  } catch (e) {
    console.warn('Delete failed:', e.message);
  }
}

async function deleteSelectedItems() {
  if (gallerySelection.size === 0) return;
  const count = gallerySelection.size;
  const confirmed = await showDeleteConfirm(`Delete ${count} selected item(s)? This cannot be undone.`);
  if (!confirmed) return;

  let deleted = 0;
  for (const filename of [...gallerySelection]) {
    const item = galleryItems.find(i => i.filename === filename);
    if (!item) continue;
    try {
      const ok = await deleteMedia(filename, item.type);
      if (ok) {
        deleted++;
        galleryItems = galleryItems.filter(i => i.filename !== filename);
        gallerySelection.delete(filename);
      }
    } catch (e) {
      console.warn(`Failed to delete ${filename}:`, e.message);
    }
  }
  gallerySelectMode = false;
  updateSelectModeUI();
  applyFilters();
}

// ── Keyboard navigation ──
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (deleteDialog.classList.contains('active')) {
      hideDeleteDialog();
    } else if (lightbox.classList.contains('active')) {
      closeLightbox();
    } else if (galleryOpen) {
      closeGallery();
    }
    return;
  }

  // Lightbox navigation
  if (lightbox.classList.contains('active')) {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      lightboxPrev();
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      lightboxNext();
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      const item = galleryFiltered[lightboxIndex];
      if (item) deleteSingleItem(item.filename, item.type);
    }
    return;
  }

  // Gallery search focus
  if (galleryOpen && !lightbox.classList.contains('active')) {
    if (e.key === '/' && document.activeElement !== gallerySearchInput) {
      e.preventDefault();
      gallerySearchInput.focus();
    }
  }
});

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

// ═══════════════════════════════════════════════════════════
//  EVENT BINDINGS — No inline handlers (CSP-safe)
// ═══════════════════════════════════════════════════════════

document.getElementById('btnGallery').addEventListener('click', () => openGallery());
document.getElementById('btnLock').addEventListener('click', () => toggleLayoutLock());
document.getElementById('btnPhoneAudio').addEventListener('click', () => togglePhoneAudio());
document.getElementById('btnCv').addEventListener('click', () => toggleCv());

// Intercom push-to-talk
btnIntercom.addEventListener('mousedown', () => intercomDown());
btnIntercom.addEventListener('mouseup', () => intercomUp());
btnIntercom.addEventListener('touchstart', (e) => { e.preventDefault(); intercomDown(); });
btnIntercom.addEventListener('touchend', (e) => { e.preventDefault(); intercomUp(); });

// Call actions — event delegation for dynamically created buttons
callActions.addEventListener('click', (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;
  if (btn.classList.contains('btn-answer')) answerCall();
  else if (btn.classList.contains('btn-ignore') || btn.classList.contains('btn-hangup')) endCall();
});

// Gallery controls
document.getElementById('btnGalleryRefresh').addEventListener('click', () => refreshGallery());
document.getElementById('btnGalleryClose').addEventListener('click', () => closeGallery());

// Gallery tabs
document.querySelectorAll('.gallery-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.gallery-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    galleryFilter = tab.dataset.filter;
    applyFilters();
  });
});

// Gallery search
gallerySearchInput.addEventListener('input', (e) => {
  gallerySearchQuery = e.target.value;
  applyFilters();
});

// Gallery select mode
btnSelectMode.addEventListener('click', () => toggleSelectMode());
btnDeleteSelected.addEventListener('click', () => deleteSelectedItems());

// Lightbox
lightbox.addEventListener('click', (e) => {
  if (e.target === lightbox || e.target.classList.contains('lightbox-stage')) closeLightbox();
});
document.getElementById('btnLightboxClose').addEventListener('click', () => closeLightbox());
document.getElementById('btnLightboxPrev').addEventListener('click', () => lightboxPrev());
document.getElementById('btnLightboxNext').addEventListener('click', () => lightboxNext());
document.getElementById('btnLightboxDownload').addEventListener('click', () => lightboxDownload());
document.getElementById('btnLightboxDelete').addEventListener('click', () => {
  const item = galleryFiltered[lightboxIndex];
  if (item) deleteSingleItem(item.filename, item.type);
});

// Delete dialog
document.getElementById('btnDeleteCancel').addEventListener('click', () => hideDeleteDialog());
document.getElementById('btnDeleteConfirm').addEventListener('click', () => {
  if (pendingDeleteResolve) {
    const resolve = pendingDeleteResolve;
    pendingDeleteResolve = null;
    deleteDialog.classList.remove('active');
    resolve(true);
  }
});

// Gallery grid — event delegation for cards, delete buttons, selection
galleryBody.addEventListener('click', (e) => {
  // Delete button
  const delBtn = e.target.closest('.gallery-delete');
  if (delBtn) {
    e.stopPropagation();
    const card = delBtn.closest('.gallery-card');
    if (card) deleteSingleItem(card.dataset.filename, card.dataset.type);
    return;
  }

  // Card click
  const card = e.target.closest('.gallery-card');
  if (!card) return;

  if (gallerySelectMode) {
    // Toggle selection
    const fn = card.dataset.filename;
    if (gallerySelection.has(fn)) {
      gallerySelection.delete(fn);
      card.classList.remove('selected');
    } else {
      gallerySelection.add(fn);
      card.classList.add('selected');
    }
    updateSelectModeUI();
    return;
  }

  // Open in lightbox — find index in filtered list
  const fn = card.dataset.filename;
  const idx = galleryFiltered.findIndex(i => i.filename === fn);
  if (idx >= 0) openLightbox(idx);
});
