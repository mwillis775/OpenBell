/*!
Doorbell System — Rust Coordination Server

High-performance local-network doorbell backend:
- WebSocket control plane for phone + Tauri dashboard
- cpal-based PC microphone capture with native ALSA/PipeWire
- UDP audio streaming for lowest-latency delivery to phone
- mDNS service advertisement for automatic discovery
*/

mod audio;
mod discovery;
mod dsp;
mod protocol;
mod relay;
mod state;
mod ws_server;

use std::net::SocketAddr;
use std::sync::Arc;
use tracing::info;

fn server_port() -> u16 {
    std::env::var("OPENBELL_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(5000)
}

pub const SERVER_PORT: u16 = 5000;

#[tokio::main]
async fn main() {
    // Initialize logging
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "doorbell_server=info,tower_http=info".into()),
        )
        .init();

    info!("==================================================");
    info!("Doorbell System — Rust Coordination Server");
    info!("==================================================");
    let port = server_port();
    info!("WebSocket + REST: http://0.0.0.0:{}", port);
    info!("Audio PC→phone UDP: port 5002");
    info!("Audio phone→PC UDP: port 5003");
    info!("Voice assistant:    UDP 5004 (→asst) / 5005 (←asst)");
    info!("==================================================");

    // Shared state
    let state = Arc::new(state::AppState::new());
    info!("Auto-answer timeout: {}s", state.auto_answer_secs);

    // Audio manager (cpal + UDP)
    let audio_mgr = audio::AudioManager::new(state.clone()).await;

    // Advertise via mDNS
    discovery::advertise(port);

    // If a direct phone MJPEG URL is provided, pull frames from it
    // (works even when the phone can't reach us via WebSocket)
    if let Ok(phone_url) = std::env::var("PHONE_STREAM_URL") {
        let proxy_state = state.clone();
        let proxy_port = port;
        tokio::spawn(async move {
            mjpeg_proxy(phone_url, proxy_state, proxy_port).await;
        });
    }

    // Build axum router
    let app = ws_server::build_router(state.clone(), audio_mgr);

    // Start serving
    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    info!("Listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await
    .unwrap();
}

// ── MJPEG pull-proxy ──────────────────────────────────────────────────

/// Pull MJPEG frames from the phone's built-in camera server and inject
/// them into the frame pipeline.  This lets the system work even when
/// the phone's APK has a stale server URL (i.e. the phone can't push
/// frames to us over WebSocket).
async fn mjpeg_proxy(url: String, state: Arc<state::AppState>, port: u16) {

    // Register a virtual device so dashboard + CV server see a camera
    let local_stream = format!("http://localhost:{}/api/stream", port);
    {
        let dev = state::DeviceInfo {
            device_ip: "proxy".into(),
            stream_url: Some(local_stream.clone()),
            capabilities: vec![
                "camera".into(),
                "doorbell_button".into(),
                "audio_playback".into(),
            ],
            device_type: "doorbell".into(),
            device_name: "Front Door".into(),
            registered_at: state::AppState::now_secs(),
            last_seen: state::AppState::now_secs(),
        };
        state.devices.write().insert("proxy".into(), dev);
        *state.stream_url.write() = Some(local_stream.clone());
        // Notify any connected dashboards
        let _ = state.broadcast_tx.send(protocol::ServerMessage::Status {
            call_state: state.call_state.read().to_string(),
            devices: serde_json::to_value(&*state.devices.read()).unwrap_or_default(),
            stream_url: Some(local_stream),
            phone_audio_muted: state.phone_audio_muted.load(std::sync::atomic::Ordering::Relaxed),
            cv_enabled: state.cv_enabled.load(std::sync::atomic::Ordering::Relaxed),
        });
    }

    info!("MJPEG proxy: pulling frames from {}", url);

    loop {
        match pull_mjpeg_frames(&url, &state).await {
            Ok(()) => info!("MJPEG proxy: stream ended, reconnecting…"),
            Err(e) => info!("MJPEG proxy: {}, reconnecting in 3s…", e),
        }
        tokio::time::sleep(std::time::Duration::from_secs(3)).await;
    }
}

async fn pull_mjpeg_frames(
    url: &str,
    state: &Arc<state::AppState>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use futures::StreamExt;

    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(10))
        .build()?;

    let resp = client.get(url).send().await?.error_for_status()?;
    let mut stream = resp.bytes_stream();
    let mut buf: Vec<u8> = Vec::with_capacity(256 * 1024);

    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        buf.extend_from_slice(&chunk);

        // Extract complete JPEG frames (SOI 0xFFD8 … EOI 0xFFD9)
        loop {
            let soi = match buf.windows(2).position(|w| w == [0xFF, 0xD8]) {
                Some(pos) => pos,
                None => {
                    buf.clear();
                    break;
                }
            };
            let after_soi = soi + 2;
            let eoi = match buf[after_soi..].windows(2).position(|w| w == [0xFF, 0xD9]) {
                Some(pos) => after_soi + pos + 2, // byte after EOI marker
                None => break,                    // incomplete frame
            };
            let frame = bytes::Bytes::copy_from_slice(&buf[soi..eoi]);
            let _ = state.frame_tx.send(Some(frame));
            buf.drain(..eoi);
        }
    }

    Err("Stream closed by remote".into())
}
