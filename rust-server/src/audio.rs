/*!
Audio subsystem — uses PipeWire (`pw-cat`) subprocesses for reliable audio routing.

Two always-running pipelines:

**Phone mic → PC speakers** (always-on, mute toggle on dashboard):
  phone → UDP :5003 → pw-cat --playback → PipeWire → speakers/headphones

**PC mic → phone speaker** (gated by intercom_active):
  pw-cat --record → PipeWire mic → UDP :5002 → phone
*/

use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UdpSocket;
use tokio::process::{Child, Command};
use tracing::{error, info, warn};

use crate::state::AppState;

/// 10 ms at 48 kHz mono i16 = 480 samples × 2 bytes = 960 bytes
const BYTES_PER_PACKET: usize = 480 * 2;
/// UDP header: 4-byte big-endian sequence number
const HEADER_SIZE: usize = 4;

pub const SAMPLE_RATE: u32 = 48_000;
pub const CHANNELS: u16 = 1;
pub const BITS_PER_SAMPLE: u16 = 16;

pub struct AudioManager {
    _outgoing_socket: Arc<UdpSocket>,
}

unsafe impl Send for AudioManager {}
unsafe impl Sync for AudioManager {}

impl AudioManager {
    pub async fn new(state: Arc<AppState>) -> Arc<Self> {
        // UDP port 5002: PC mic audio → phone (intercom)
        let outgoing_socket = UdpSocket::bind("0.0.0.0:5002")
            .await
            .expect("Failed to bind outgoing UDP audio socket on port 5002");
        info!("Audio outgoing UDP socket bound on port 5002 (PC→phone)");
        let outgoing_socket = Arc::new(outgoing_socket);

        // UDP port 5003: phone mic audio → PC speakers (always-on)
        let incoming_socket = UdpSocket::bind("0.0.0.0:5003")
            .await
            .expect("Failed to bind incoming UDP audio socket on port 5003");
        info!("Audio incoming UDP socket bound on port 5003 (phone→PC)");
        let incoming_socket = Arc::new(incoming_socket);

        // ── Phone mic → PC speakers (always-on) ──
        let mute_flag = state.phone_audio_muted.clone();
        tokio::spawn(async move {
            phone_to_speakers(incoming_socket, mute_flag).await;
        });

        // ── PC mic → Phone speaker (intercom, gated by state) ──
        let mic_state = state.clone();
        let mic_socket = outgoing_socket.clone();
        tokio::spawn(async move {
            mic_to_phone(mic_state, mic_socket).await;
        });

        Arc::new(Self {
            _outgoing_socket: outgoing_socket,
        })
    }

    /// No-op: mic capture runs continuously via pw-cat;
    /// sending is gated by `intercom_active` in `mic_to_phone`.
    pub fn start_capture(&self) {}

    /// No-op: same reason.
    pub fn stop_capture(&self) {}
}

// ═══════════════════════════════════════════════════════════════
//  Phone mic → PC speakers (always-on, via pw-cat --playback)
// ═══════════════════════════════════════════════════════════════

/// Receives phone audio via UDP, pipes it into `pw-cat --playback` which
/// routes through PipeWire to whatever output device is active (Bluetooth,
/// HDMI, built-in speakers, etc.).
async fn phone_to_speakers(socket: Arc<UdpSocket>, mute_flag: Arc<AtomicBool>) {
    info!("Phone→speaker pipeline starting (phone→PC via pw-cat)");

    // Outer loop: restart pw-cat if it dies
    loop {
        match spawn_speaker_process() {
            Ok(mut child) => {
                let mut stdin = child.stdin.take().expect("pw-cat stdin");
                info!("Speaker output: pw-cat --playback started (PipeWire)");

                let mut buf = [0u8; 2048];
                let mut total: u64 = 0;
                let mut count: u64 = 0;
                // Static silence buffer for mute mode
                let zeros = [0u8; 2048];

                loop {
                    match socket.recv_from(&mut buf).await {
                        Ok((len, _addr)) => {
                            if len <= HEADER_SIZE {
                                continue;
                            }

                            let pcm_len = len - HEADER_SIZE;
                            total += pcm_len as u64;
                            count += 1;

                            // When muted, feed silence to keep the stream alive
                            let data = if mute_flag.load(Ordering::Relaxed) {
                                &zeros[..pcm_len]
                            } else {
                                &buf[HEADER_SIZE..len]
                            };

                            if let Err(e) = stdin.write_all(data).await {
                                warn!("Speaker write error: {} — restarting pw-cat", e);
                                break;
                            }

                            if count % 1000 == 0 {
                                info!(
                                    "Phone→speaker: {} KB piped, {} packets",
                                    total / 1024,
                                    count
                                );
                            }
                        }
                        Err(e) => {
                            warn!("Phone audio recv error: {}", e);
                            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                        }
                    }
                }

                let _ = child.kill().await;
                warn!("pw-cat --playback exited — restarting in 1 s");
            }
            Err(e) => {
                error!("Failed to start pw-cat --playback: {} — retrying in 5 s", e);
            }
        }
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
}

fn spawn_speaker_process() -> Result<Child, std::io::Error> {
    Command::new("pw-cat")
        .args([
            "--playback",
            "--raw",
            "--format",
            "s16",
            "--rate",
            &SAMPLE_RATE.to_string(),
            "--channels",
            "1",
            "-",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
}

// ═══════════════════════════════════════════════════════════════
//  PC mic → Phone speaker (intercom via pw-cat --record)
// ═══════════════════════════════════════════════════════════════

/// Continuously captures from the default PipeWire input device.
/// Only sends UDP packets to the phone when `intercom_active` is true
/// and `phone_audio_addr` is set.
async fn mic_to_phone(state: Arc<AppState>, socket: Arc<UdpSocket>) {
    info!("Mic→phone pipeline starting (PC mic via pw-cat)");

    // Outer loop: restart pw-cat if it dies
    loop {
        match spawn_mic_process() {
            Ok(mut child) => {
                let mut stdout = child.stdout.take().expect("pw-cat stdout");
                info!("Mic capture: pw-cat --record started (PipeWire)");

                let mut seq: u32 = 0;
                let mut pkt = vec![0u8; HEADER_SIZE + BYTES_PER_PACKET];
                let mut read_buf = vec![0u8; BYTES_PER_PACKET];
                let mut sent_count: u64 = 0;
                let mut skip_log: u64 = 0;

                loop {
                    // Read exactly one packet worth of PCM from mic
                    if let Err(e) = stdout.read_exact(&mut read_buf).await {
                        warn!("Mic read error: {} — restarting pw-cat", e);
                        break;
                    }

                    // Only send when intercom is active
                    if !*state.intercom_active.read() {
                        // Just discard — mic data flows continuously
                        continue;
                    }

                    let phone_addr = match *state.phone_audio_addr.read() {
                        Some(a) => a,
                        None => {
                            skip_log += 1;
                            if skip_log % 500 == 1 {
                                warn!("Mic: intercom active but no phone_audio_addr set");
                            }
                            continue;
                        }
                    };

                    // Build [4-byte seq | PCM data] and send
                    pkt[..HEADER_SIZE].copy_from_slice(&seq.to_be_bytes());
                    pkt[HEADER_SIZE..].copy_from_slice(&read_buf);

                    if let Err(e) = socket.send_to(&pkt, phone_addr).await {
                        warn!("Mic UDP send error: {}", e);
                    }

                    seq = seq.wrapping_add(1);
                    sent_count += 1;

                    if sent_count % 500 == 0 {
                        info!(
                            "Mic→phone: {} packets sent to {} (seq={})",
                            sent_count, phone_addr, seq
                        );
                    }
                }

                let _ = child.kill().await;
                warn!("pw-cat --record exited — restarting in 1 s");
            }
            Err(e) => {
                error!("Failed to start pw-cat --record: {} — retrying in 5 s", e);
            }
        }
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
}

fn spawn_mic_process() -> Result<Child, std::io::Error> {
    Command::new("pw-cat")
        .args([
            "--record",
            "--raw",
            "--format",
            "s16",
            "--rate",
            &SAMPLE_RATE.to_string(),
            "--channels",
            "1",
            "-",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
}

// ═══════════════════════════════════════════════════════════════
//  Doorbell chime — plays WAV through GP104 HDMI speakers
// ═══════════════════════════════════════════════════════════════

/// GP104 HDA PipeWire sink name
const DOORBELL_SINK: &str = "alsa_output.pci-0000_01_00.1.hdmi-stereo";

/// Path to the bundled doorbell WAV (relative to the server binary's cwd)
const DOORBELL_WAV: &str = "assets/doorbell.wav";

/// Play the ding-dong chime on the GP104 HDMI speakers.
/// Spawns pw-play as a fire-and-forget background task.
pub fn play_doorbell_chime() {
    // Resolve path relative to the crate root / working dir
    let wav_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(DOORBELL_WAV);

    if !wav_path.exists() {
        // Fallback: try relative to cwd
        let cwd_path = std::path::PathBuf::from(DOORBELL_WAV);
        if !cwd_path.exists() {
            error!("Doorbell WAV not found at {:?} or {:?}", wav_path, cwd_path);
            return;
        }
        spawn_chime(&cwd_path);
        return;
    }
    spawn_chime(&wav_path);
}

fn spawn_chime(wav: &std::path::Path) {
    info!("Playing doorbell chime on {} via pw-play", DOORBELL_SINK);
    match std::process::Command::new("pw-play")
        .args([
            "--target",
            DOORBELL_SINK,
            "--volume",
            "0.9",
        ])
        .arg(wav)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(_child) => {
            // Fire-and-forget — pw-play exits when done
            info!("Doorbell chime subprocess started");
        }
        Err(e) => {
            error!("Failed to spawn pw-play for doorbell chime: {}", e);
        }
    }
}
