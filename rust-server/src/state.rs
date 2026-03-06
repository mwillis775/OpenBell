use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, AtomicU32};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use parking_lot::RwLock;
use serde::Serialize;
use tokio::sync::broadcast;

use crate::protocol::{CallState, ServerMessage};

/// Device info from a registered phone
#[derive(Debug, Clone, Serialize)]
pub struct DeviceInfo {
    pub device_ip: String,
    pub stream_url: Option<String>,
    pub capabilities: Vec<String>,
    pub device_type: String,
    pub device_name: String,
    pub registered_at: f64,
    pub last_seen: f64,
}

/// A single call record
#[derive(Debug, Clone, Serialize)]
pub struct CallRecord {
    pub call_id: String,
    pub started_at: f64,
    pub ended_at: Option<f64>,
    pub was_answered: bool,
    pub duration: f64,
}

/// Shared application state
pub struct AppState {
    pub call_state: RwLock<CallState>,
    pub current_call_id: RwLock<Option<String>>,
    pub call_started_at: RwLock<f64>,
    pub call_was_answered: RwLock<bool>,
    pub devices: RwLock<HashMap<String, DeviceInfo>>,
    pub call_history: RwLock<Vec<CallRecord>>,
    /// Broadcast channel for server→client messages
    pub broadcast_tx: broadcast::Sender<ServerMessage>,
    /// Phone's UDP address for audio streaming
    pub phone_audio_addr: RwLock<Option<SocketAddr>>,
    /// Phone's WebSocket IP (learned on registration)
    pub phone_ip: RwLock<Option<String>>,
    /// Phone's camera stream URL
    pub stream_url: RwLock<Option<String>>,
    /// Whether the PC intercom is currently active (PC mic → phone)
    pub intercom_active: RwLock<bool>,
    /// Mute flag for phone→PC audio (checked by cpal output callback)
    pub phone_audio_muted: Arc<AtomicBool>,
    /// Whether the voice assistant is currently handling a call
    pub assistant_active: Arc<AtomicBool>,
    /// Current RMS level of phone→speaker audio (shared with mic pipeline for ducking)
    pub speaker_rms: Arc<AtomicU32>,
    /// Whether CV detection is enabled (can be toggled from dashboard)
    pub cv_enabled: Arc<AtomicBool>,
    /// Seconds to wait before auto-answering with the voice assistant
    pub auto_answer_secs: u64,
}

impl AppState {
    pub fn new() -> Self {
        let (tx, _) = broadcast::channel(256);
        Self {
            call_state: RwLock::new(CallState::Idle),
            current_call_id: RwLock::new(None),
            call_started_at: RwLock::new(0.0),
            call_was_answered: RwLock::new(false),
            devices: RwLock::new(HashMap::new()),
            call_history: RwLock::new(Vec::new()),
            broadcast_tx: tx,
            phone_audio_addr: RwLock::new(None),
            phone_ip: RwLock::new(None),
            stream_url: RwLock::new(None),
            intercom_active: RwLock::new(false),
            phone_audio_muted: Arc::new(AtomicBool::new(false)),
            assistant_active: Arc::new(AtomicBool::new(false)),
            speaker_rms: Arc::new(AtomicU32::new(0_f32.to_bits())),
            cv_enabled: Arc::new(AtomicBool::new(true)),
            auto_answer_secs: std::env::var("OPENBELL_AUTO_ANSWER_SECS")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(30),
        }
    }

    pub fn now_secs() -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64()
    }

    /// Start a new ringing call
    pub fn ring(&self) -> (String, CallState) {
        let mut state = self.call_state.write();
        if *state != CallState::Idle && *state != CallState::Ended {
            return (
                self.current_call_id.read().clone().unwrap_or_default(),
                *state,
            );
        }
        let call_id = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
        *state = CallState::Ringing;
        *self.current_call_id.write() = Some(call_id.clone());
        *self.call_started_at.write() = Self::now_secs();
        *self.call_was_answered.write() = false;

        let _ = self.broadcast_tx.send(ServerMessage::CallState {
            state: "ringing".into(),
            call_id: Some(call_id.clone()),
        });

        (call_id, CallState::Ringing)
    }

    /// Answer the ringing call
    pub fn answer(&self) -> CallState {
        let mut state = self.call_state.write();
        if *state != CallState::Ringing {
            return *state;
        }
        *state = CallState::Answered;
        *self.call_was_answered.write() = true;

        let call_id = self.current_call_id.read().clone();
        let _ = self.broadcast_tx.send(ServerMessage::CallState {
            state: "answered".into(),
            call_id,
        });

        CallState::Answered
    }

    /// End the current call, return a record
    pub fn end_call(&self) -> Option<CallRecord> {
        let mut state = self.call_state.write();
        let prev = *state;
        if prev == CallState::Idle {
            return None;
        }
        *state = CallState::Idle;

        let now = Self::now_secs();
        let started = *self.call_started_at.read();
        let record = CallRecord {
            call_id: self.current_call_id.read().clone().unwrap_or_default(),
            started_at: started,
            ended_at: Some(now),
            was_answered: *self.call_was_answered.read(),
            duration: now - started,
        };

        self.call_history.write().push(record.clone());
        *self.current_call_id.write() = None;
        // Don't clear phone_audio_addr — phone streams audio 24/7
        // *self.phone_audio_addr.write() = None;

        let _ = self.broadcast_tx.send(ServerMessage::CallState {
            state: "idle".into(),
            call_id: None,
        });
        let _ = self.broadcast_tx.send(ServerMessage::StopAudio);

        Some(record)
    }
}
