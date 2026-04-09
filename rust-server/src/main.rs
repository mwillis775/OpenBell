/*!
Doorbell System — Rust Coordination Server

High-performance local-network doorbell backend:
- WebSocket control plane for phone + Electron dashboard
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
