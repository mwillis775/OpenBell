use std::net::SocketAddr;
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{
        ws::{Message, WebSocket},
        ConnectInfo, State, WebSocketUpgrade,
    },
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use bytes::Bytes;
use futures::{SinkExt, StreamExt};
use tower_http::cors::CorsLayer;
use tracing::{info, warn};

use crate::audio::{self, AudioManager};
use crate::protocol::{CallState, ClientMessage, ServerMessage};
use crate::state::AppState;

use std::sync::atomic::Ordering;
use std::time::Duration;

/// Build the axum router
pub fn build_router(state: Arc<AppState>, audio_mgr: Arc<AudioManager>) -> Router {
    let shared = Arc::new(WsState {
        app: state,
        audio: audio_mgr,
    });

    Router::new()
        .route("/ws", get(ws_handler))
        .route("/api/status", get(api_status))
        .route("/api/call/status", get(api_call_status))
        .route("/api/cv/event", post(cv_event))
        .route("/api/cv/status", get(cv_status))
        .route("/api/cv/toggle", post(cv_toggle))
        .route("/api/stream", get(api_stream))
        .route("/api/snapshot", get(api_snapshot))
        .with_state(shared)
        .layer(CorsLayer::permissive())
}

#[derive(Clone)]
struct WsState {
    app: Arc<AppState>,
    audio: Arc<AudioManager>,
}

// ── REST endpoints ──

async fn api_status(State(ws): State<Arc<WsState>>) -> Json<serde_json::Value> {
    let state = &ws.app;
    let call_state = state.call_state.read().to_string();
    let devices = serde_json::to_value(&*state.devices.read()).unwrap_or_default();
    let stream_url = state.stream_url.read().clone();

    Json(serde_json::json!({
        "status": "online",
        "call_state": call_state,
        "devices": devices,
        "stream_url": stream_url,
        "phone_audio_muted": state.phone_audio_muted.load(Ordering::Relaxed),
        "cv_enabled": state.cv_enabled.load(Ordering::Relaxed),
    }))
}

async fn api_call_status(State(ws): State<Arc<WsState>>) -> Json<serde_json::Value> {
    let state = &ws.app;
    let call_state = state.call_state.read().to_string();
    let call_id = state.current_call_id.read().clone();

    Json(serde_json::json!({
        "state": call_state,
        "call_id": call_id,
    }))
}

/// GET /api/cv/status — returns whether CV detection is enabled
async fn cv_status(State(ws): State<Arc<WsState>>) -> Json<serde_json::Value> {
    let enabled = ws.app.cv_enabled.load(Ordering::Relaxed);
    Json(serde_json::json!({ "enabled": enabled }))
}

/// POST /api/cv/toggle — toggle CV detection on/off
async fn cv_toggle(
    State(ws): State<Arc<WsState>>,
    Json(payload): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let enabled = payload["enabled"].as_bool().unwrap_or(true);
    ws.app.cv_enabled.store(enabled, Ordering::Relaxed);
    info!("CV detection toggled: enabled={}", enabled);
    let _ = ws.app.broadcast_tx.send(ServerMessage::CvState { enabled });
    Json(serde_json::json!({ "status": "ok", "enabled": enabled }))
}

// ── Camera stream endpoints ──

const MJPEG_BOUNDARY: &str = "openbell-frame";

/// GET /api/stream — MJPEG multipart stream from the phone's camera
async fn api_stream(State(ws): State<Arc<WsState>>) -> Response {
    let mut rx = ws.app.frame_rx.clone();
    let stream = async_stream::stream! {
        loop {
            // Wait for a new frame
            if rx.changed().await.is_err() {
                break;
            }
            let frame: Option<Bytes> = rx.borrow_and_update().clone();
            if let Some(jpeg) = frame {
                let header = format!(
                    "--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
                    jpeg.len()
                );
                yield Ok::<Bytes, std::convert::Infallible>(Bytes::from(header));
                yield Ok(jpeg);
                yield Ok(Bytes::from_static(b"\r\n"));
            }
        }
    };

    Response::builder()
        .status(StatusCode::OK)
        .header(
            header::CONTENT_TYPE,
            format!("multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}"),
        )
        .header(header::CACHE_CONTROL, "no-cache, no-store, must-revalidate")
        .body(Body::from_stream(stream))
        .unwrap()
}

/// GET /api/snapshot — single JPEG frame
async fn api_snapshot(State(ws): State<Arc<WsState>>) -> Response {
    let frame = ws.app.frame_rx.borrow().clone();
    match frame {
        Some(jpeg) => Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "image/jpeg")
            .header(header::CONTENT_LENGTH, jpeg.len().to_string())
            .body(Body::from(jpeg))
            .unwrap(),
        None => Response::builder()
            .status(StatusCode::SERVICE_UNAVAILABLE)
            .body(Body::from("No frame available"))
            .unwrap(),
    }
}

// ── WebSocket handler ──

async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<WsState>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
) -> impl IntoResponse {
    info!("WebSocket connection from {}", addr);
    ws.on_upgrade(move |socket| handle_socket(socket, state, addr))
}

async fn handle_socket(socket: WebSocket, ws: Arc<WsState>, addr: SocketAddr) {
    let (mut sender, mut receiver) = socket.split();

    // Subscribe to broadcast channel for outgoing messages
    let mut broadcast_rx = ws.app.broadcast_tx.subscribe();

    // Send initial status
    let status = ServerMessage::Status {
        call_state: ws.app.call_state.read().to_string(),
        devices: serde_json::to_value(&*ws.app.devices.read()).unwrap_or_default(),
        stream_url: ws.app.stream_url.read().clone(),
        phone_audio_muted: ws.app.phone_audio_muted.load(Ordering::Relaxed),
        cv_enabled: ws.app.cv_enabled.load(Ordering::Relaxed),
    };
    let _ = sender
        .send(Message::Text(serde_json::to_string(&status).unwrap().into()))
        .await;

    // Auto-register fallback: if a non-localhost client connects and doesn't
    // send a Register message within 5 seconds, assume it's the doorbell phone
    // and register it automatically.
    if !addr.ip().is_loopback() {
        let auto_ws = ws.clone();
        let auto_ip = addr.ip().to_string();
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
            // Check if ANY device has already registered (the phone may
            // register under a different IP than the WebSocket source IP,
            // e.g. 192.168.4.x vs 192.168.0.x on dual-NIC setups).
            let has_any_device = !auto_ws.app.devices.read().is_empty();
            let has_stream = auto_ws.app.stream_url.read().is_some();
            if has_any_device || has_stream {
                info!(
                    "Skipping auto-register for {} — a device is already registered",
                    auto_ip
                );
                return;
            }
            // Stream is served locally via /api/stream (phone pushes frames over WS)
            let stream = format!("http://localhost:{}/api/stream", crate::SERVER_PORT);
            info!("Auto-registering unregistered phone {} (fallback)", auto_ip);
            let dev = crate::state::DeviceInfo {
                device_ip: auto_ip.clone(),
                stream_url: Some(stream.clone()),
                capabilities: vec!["camera".into(), "doorbell_button".into(), "audio_playback".into()],
                device_type: "doorbell".into(),
                device_name: "Front Door".into(),
                registered_at: AppState::now_secs(),
                last_seen: AppState::now_secs(),
            };
            auto_ws.app.devices.write().insert(auto_ip.clone(), dev);
            *auto_ws.app.phone_ip.write() = Some(auto_ip);
            *auto_ws.app.stream_url.write() = Some(stream);
            let _ = auto_ws.app.broadcast_tx.send(ServerMessage::Status {
                call_state: auto_ws.app.call_state.read().to_string(),
                devices: serde_json::to_value(&*auto_ws.app.devices.read()).unwrap_or_default(),
                stream_url: auto_ws.app.stream_url.read().clone(),
                phone_audio_muted: auto_ws.app.phone_audio_muted.load(Ordering::Relaxed),
                cv_enabled: auto_ws.app.cv_enabled.load(Ordering::Relaxed),
            });
        });
    }

    // Spawn task for broadcast → client
    let mut send_task = tokio::spawn(async move {
        loop {
            match broadcast_rx.recv().await {
                Ok(msg) => {
                    if let Ok(json) = serde_json::to_string(&msg) {
                        if sender.send(Message::Text(json.into())).await.is_err() {
                            break;
                        }
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    warn!("Broadcast receiver lagged, skipped {} messages", n);
                    // Continue — don't kill the connection over missed broadcasts
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            }
        }
    });

    // Handle incoming messages from client
    let ws_clone = ws.clone();
    let client_addr = addr;
    let mut recv_task = tokio::spawn(async move {
        let mut registered_via_frame = false;
        while let Some(Ok(msg)) = receiver.next().await {
            match msg {
                Message::Text(text) => {
                    handle_client_message(&ws_clone, &text, client_addr).await;
                }
                Message::Binary(data) => {
                    // Binary messages are JPEG camera frames from the phone
                    if data.len() > 2 && data[0] == 0xFF && data[1] == 0xD8 {
                        let _ = ws_clone.app.frame_tx.send(Some(Bytes::from(data.to_vec())));

                        // Auto-register on first frame if no device registered yet
                        if !registered_via_frame && ws_clone.app.stream_url.read().is_none() {
                            registered_via_frame = true;
                            let ip = client_addr.ip().to_string();
                            let stream = format!("http://localhost:{}/api/stream", crate::SERVER_PORT);
                            info!("Auto-registering camera from {} (first frame received)", ip);
                            let dev = crate::state::DeviceInfo {
                                device_ip: ip.clone(),
                                stream_url: Some(stream.clone()),
                                capabilities: vec!["camera".into(), "doorbell_button".into(), "audio_playback".into()],
                                device_type: "doorbell".into(),
                                device_name: "Front Door".into(),
                                registered_at: AppState::now_secs(),
                                last_seen: AppState::now_secs(),
                            };
                            ws_clone.app.devices.write().insert(ip.clone(), dev);
                            *ws_clone.app.phone_ip.write() = Some(ip);
                            *ws_clone.app.stream_url.write() = Some(stream);
                            let _ = ws_clone.app.broadcast_tx.send(ServerMessage::Status {
                                call_state: ws_clone.app.call_state.read().to_string(),
                                devices: serde_json::to_value(&*ws_clone.app.devices.read()).unwrap_or_default(),
                                stream_url: ws_clone.app.stream_url.read().clone(),
                                phone_audio_muted: ws_clone.app.phone_audio_muted.load(Ordering::Relaxed),
                                cv_enabled: ws_clone.app.cv_enabled.load(Ordering::Relaxed),
                            });
                        }
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
    });

    // Wait for either task to finish
    tokio::select! {
        _ = &mut send_task => { recv_task.abort(); }
        _ = &mut recv_task => { send_task.abort(); }
    }

    info!("WebSocket disconnected: {}", addr);
}

async fn handle_client_message(ws: &Arc<WsState>, text: &str, addr: SocketAddr) {
    let msg: ClientMessage = match serde_json::from_str(text) {
        Ok(m) => m,
        Err(e) => {
            warn!("Invalid message from {}: {} — {}", addr, e, text);
            return;
        }
    };

    let state = &ws.app;

    match msg {
        ClientMessage::Register {
            device_ip,
            stream_url: _,
            capabilities,
            device_type,
            device_name,
        } => {
            let ip = device_ip.unwrap_or_else(|| addr.ip().to_string());
            // Stream is always served locally (phone pushes frames over WS)
            let local_stream = format!("http://localhost:{}/api/stream", crate::SERVER_PORT);
            let dev = crate::state::DeviceInfo {
                device_ip: ip.clone(),
                stream_url: Some(local_stream.clone()),
                capabilities: capabilities.unwrap_or_default(),
                device_type: device_type.unwrap_or_else(|| "doorbell".into()),
                device_name: device_name.unwrap_or_else(|| "Front Door".into()),
                registered_at: AppState::now_secs(),
                last_seen: AppState::now_secs(),
            };
            state.devices.write().insert(ip.clone(), dev);
            *state.phone_ip.write() = Some(ip.clone());
            *state.stream_url.write() = Some(local_stream);
            info!("Device registered: {} from {}", ip, addr);

            // Notify dashboards of updated status
            let _ = state.broadcast_tx.send(ServerMessage::Status {
                call_state: state.call_state.read().to_string(),
                devices: serde_json::to_value(&*state.devices.read()).unwrap_or_default(),
                stream_url: state.stream_url.read().clone(),
                phone_audio_muted: state.phone_audio_muted.load(Ordering::Relaxed),
                cv_enabled: state.cv_enabled.load(Ordering::Relaxed),
            });
        }

        ClientMessage::DoorbellPress => {
            info!("DOORBELL PRESSED from {}", addr);
            let (call_id, _) = state.ring();
            let _ = state.broadcast_tx.send(ServerMessage::DoorbellPress {
                timestamp: AppState::now_secs(),
            });
            // Play ding-dong on GP104 HDMI speakers
            audio::play_doorbell_chime();
            // Trigger the physical doorbell relay (if configured)
            crate::relay::trigger_physical_doorbell();
            // Start video recording on CV server
            notify_cv_recording(true);
            // The ring() already broadcasts CallState::Ringing
            info!("Call {} started — ringing", call_id);

            // Spawn auto-answer timer for voice assistant
            let timeout = state.auto_answer_secs;
            let auto_ws = ws.clone();
            tokio::spawn(async move {
                tokio::time::sleep(Duration::from_secs(timeout)).await;
                let current = *auto_ws.app.call_state.read();
                if current == CallState::Ringing {
                    activate_assistant(&auto_ws);
                }
            });
        }

        ClientMessage::AudioReady { udp_port } => {
            // Use the registered device_ip if available — the phone's WS
            // source IP (e.g. 192.168.0.x via NAT) may differ from its
            // actual reachable IP (e.g. 192.168.4.x on a different NIC).
            let phone_ip = state
                .phone_ip
                .read()
                .as_ref()
                .and_then(|ip| ip.parse::<std::net::IpAddr>().ok())
                .unwrap_or_else(|| addr.ip());
            let audio_addr = SocketAddr::new(phone_ip, udp_port);
            *state.phone_audio_addr.write() = Some(audio_addr);
            info!("Phone audio ready at {}", audio_addr);
        }

        ClientMessage::Heartbeat => {
            let ip = addr.ip().to_string();
            if let Some(dev) = state.devices.write().get_mut(&ip) {
                dev.last_seen = AppState::now_secs();
            }
        }

        ClientMessage::AnswerCall => {
            info!("Call answered from dashboard {}", addr);
            let new_state = state.answer();
            if new_state == CallState::Answered {
                *state.intercom_active.write() = true;
                // Start audio capture
                ws.audio.start_capture();
                // Tell phone to start audio
                let _ = state.broadcast_tx.send(ServerMessage::StartAudio {
                    sample_rate: audio::SAMPLE_RATE,
                    channels: audio::CHANNELS,
                    bits_per_sample: audio::BITS_PER_SAMPLE,
                });
            }
        }

        ClientMessage::EndCall => {
            info!("Call ended from {}", addr);
            ws.audio.stop_capture();
            *state.intercom_active.write() = false;
            state.assistant_active.store(false, Ordering::Relaxed);
            // Stop video recording on CV server
            notify_cv_recording(false);
            if let Some(record) = state.end_call() {
                info!(
                    "Call {} ended — duration {:.1}s, answered={}",
                    record.call_id, record.duration, record.was_answered
                );
            }
        }

        ClientMessage::IntercomStart => {
            info!("Intercom START from {}", addr);
            *state.intercom_active.write() = true;
            ws.audio.start_capture();
            let _ = state.broadcast_tx.send(ServerMessage::IntercomState { active: true });
            // Tell phone to start receiving audio
            let _ = state.broadcast_tx.send(ServerMessage::StartAudio {
                sample_rate: audio::SAMPLE_RATE,
                channels: audio::CHANNELS,
                bits_per_sample: audio::BITS_PER_SAMPLE,
            });
        }

        ClientMessage::IntercomStop => {
            info!("Intercom STOP from {}", addr);
            *state.intercom_active.write() = false;
            ws.audio.stop_capture();
            let _ = state.broadcast_tx.send(ServerMessage::IntercomState { active: false });
            let _ = state.broadcast_tx.send(ServerMessage::StopAudio);
        }

        ClientMessage::TogglePhoneAudio { muted } => {
            info!("Phone audio mute={} from {}", muted, addr);
            state.phone_audio_muted.store(muted, Ordering::Relaxed);
            let _ = state.broadcast_tx.send(ServerMessage::PhoneAudioMute { muted });
        }

        ClientMessage::ToggleCv { enabled } => {
            info!("CV detection enabled={} from {}", enabled, addr);
            state.cv_enabled.store(enabled, Ordering::Relaxed);
            let _ = state.broadcast_tx.send(ServerMessage::CvState { enabled });
        }

        ClientMessage::CvDetection {
            event_type,
            timestamp,
            person_count,
            max_confidence,
            snapshot_file,
            ..
        } => {
            handle_cv_event(state, &event_type, timestamp, person_count, max_confidence, snapshot_file, None);
        }
    }
}

// ── CV event HTTP endpoint ──

/// POST /api/cv/event — receives detection events from the Python CV sidecar
async fn cv_event(
    State(ws): State<Arc<WsState>>,
    Json(payload): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let event_type = payload["event_type"].as_str().unwrap_or("unknown");
    let timestamp = payload["timestamp"].as_f64().unwrap_or_else(AppState::now_secs);
    let person_count = payload["person_count"].as_u64().unwrap_or(0) as u32;
    let max_confidence = payload["max_confidence"].as_f64().unwrap_or(0.0);
    let snapshot_file = payload["snapshot_file"].as_str().map(String::from);
    let identities: Option<Vec<String>> = payload["identities"]
        .as_array()
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect());

    handle_cv_event(
        &ws.app,
        event_type,
        timestamp,
        person_count,
        max_confidence,
        snapshot_file,
        identities,
    );

    Json(serde_json::json!({ "status": "ok" }))
}

/// Shared logic for CV events (from both WS and HTTP)
fn handle_cv_event(
    state: &AppState,
    event_type: &str,
    timestamp: f64,
    person_count: u32,
    max_confidence: f64,
    snapshot_file: Option<String>,
    identities: Option<Vec<String>>,
) {
    match event_type {
        "person_detected" => {
            info!(
                "CV: Person detected — {} person(s), conf={:.2}, snapshot={:?}, identities={:?}",
                person_count, max_confidence, snapshot_file, identities
            );
            let _ = state.broadcast_tx.send(ServerMessage::PersonDetected {
                timestamp,
                person_count,
                max_confidence,
                snapshot_file,
                identities,
            });
        }
        "person_left" => {
            info!("CV: Person left frame");
            let _ = state.broadcast_tx.send(ServerMessage::PersonLeft { timestamp });
        }
        other => {
            warn!("CV: Unknown event type: {}", other);
        }
    }
}

// ── Voice assistant auto-answer ──

/// Activate the voice assistant — auto-answer the call, start audio
/// to the phone, and notify all clients.
fn activate_assistant(ws: &WsState) {
    let state = &ws.app;
    let mut cs = state.call_state.write();
    if *cs != CallState::Ringing {
        return;
    }
    info!("Auto-answer timeout — activating voice assistant");
    *cs = CallState::Answered;
    drop(cs);

    *state.call_was_answered.write() = true;
    state.assistant_active.store(true, Ordering::Relaxed);

    // Tell phone to start sending/receiving audio
    let _ = state.broadcast_tx.send(ServerMessage::StartAudio {
        sample_rate: audio::SAMPLE_RATE,
        channels: audio::CHANNELS,
        bits_per_sample: audio::BITS_PER_SAMPLE,
    });

    // Broadcast call state change
    let call_id = state.current_call_id.read().clone();
    let _ = state.broadcast_tx.send(ServerMessage::CallState {
        state: "answered".into(),
        call_id,
    });

    // Notify voice assistant + dashboards
    let _ = state.broadcast_tx.send(ServerMessage::AssistantActivate {
        timestamp: AppState::now_secs(),
    });
}

// ── CV server recording control ──

/// Tell the CV server to start or stop video recording.
fn notify_cv_recording(start: bool) {
    let endpoint = if start { "start" } else { "stop" };
    let url = format!("http://localhost:5100/recording/{}", endpoint);
    tokio::spawn(async move {
        let client = reqwest::Client::new();
        match client.post(&url).send().await {
            Ok(resp) if resp.status().is_success() => {
                info!("CV recording {}", endpoint);
            }
            Ok(resp) => {
                warn!("CV recording {} returned {}", endpoint, resp.status());
            }
            Err(e) => {
                warn!("Failed to notify CV recording {}: {}", endpoint, e);
            }
        }
    });
}
