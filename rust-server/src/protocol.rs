use serde::{Deserialize, Serialize};

// ── Messages from clients (phone or dashboard) to server ──

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMessage {
    /// Phone registers itself
    Register {
        device_ip: Option<String>,
        stream_url: Option<String>,
        capabilities: Option<Vec<String>>,
        device_type: Option<String>,
        device_name: Option<String>,
    },
    /// Doorbell button pressed on phone
    DoorbellPress,
    /// Phone ready to receive audio on this UDP port
    AudioReady { udp_port: u16 },
    /// Heartbeat from phone
    Heartbeat,
    /// Dashboard: answer the ringing call
    AnswerCall,
    /// Dashboard: end the current call
    EndCall,
    /// Dashboard: start intercom (PC mic → phone speaker)
    IntercomStart,
    /// Dashboard: stop intercom
    IntercomStop,
    /// Dashboard: mute/unmute phone→PC audio
    TogglePhoneAudio { muted: bool },
    /// Dashboard: enable/disable CV detection
    ToggleCv { enabled: bool },
    /// CV server: person detection event
    CvDetection {
        event_type: String,
        timestamp: f64,
        person_count: u32,
        max_confidence: f64,
        snapshot_file: Option<String>,
        detections: Vec<serde_json::Value>,
    },
}

// ── Messages from server to clients ──

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    /// Acknowledge registration
    Registered { status: String },
    /// Call state changed
    CallState {
        state: String,
        call_id: Option<String>,
    },
    /// Doorbell was pressed (sent to dashboards)
    DoorbellPress { timestamp: f64 },
    /// Full status snapshot
    Status {
        call_state: String,
        devices: serde_json::Value,
        stream_url: Option<String>,
        phone_audio_muted: bool,
        cv_enabled: bool,
    },
    /// Server is starting audio – phone should open UDP
    StartAudio {
        sample_rate: u32,
        channels: u16,
        bits_per_sample: u16,
    },
    /// Stop audio playback
    StopAudio,
    /// Intercom state changed
    IntercomState { active: bool },
    /// Phone audio mute state changed
    PhoneAudioMute { muted: bool },
    /// CV detection enabled/disabled
    CvState { enabled: bool },
    /// Error
    Error { message: String },
    /// Person detected by CV
    PersonDetected {
        timestamp: f64,
        person_count: u32,
        max_confidence: f64,
        snapshot_file: Option<String>,
    },
    /// Person left (no longer in frame)
    PersonLeft { timestamp: f64 },
    /// Voice assistant activated (auto-answer)
    AssistantActivate { timestamp: f64 },
    /// Voice assistant session ended
    AssistantDeactivate { timestamp: f64 },
}

// ── Call states ──

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CallState {
    Idle,
    Ringing,
    Answered,
    Ended,
}

impl std::fmt::Display for CallState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CallState::Idle => write!(f, "idle"),
            CallState::Ringing => write!(f, "ringing"),
            CallState::Answered => write!(f, "answered"),
            CallState::Ended => write!(f, "ended"),
        }
    }
}
