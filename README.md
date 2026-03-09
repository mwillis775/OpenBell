# OpenBell

**Turn a $50 Android phone into a private doorbell camera that never phones home.**

No cloud. No subscriptions. No facial recognition data sent anywhere. Everything stays on your local WiFi network — period.

<p align="center">
  <img src="icon.png" alt="OpenBell" width="128">
</p>

---

## What Is This?

OpenBell is an open-source doorbell system made of three pieces:

1. **An old Android phone** mounted at your front door (the doorbell)
2. **A Rust server** running on any PC/laptop on your network (the brain)
3. **An Electron dashboard** on that same PC (how you answer the door)

When a visitor taps the bell on the phone screen, your PC plays a ding-dong chime through your speakers, shows the camera feed, and lets you talk back through intercom. That's it. No accounts, no internet required after setup.

---

## Why?

Every Ring, Nest, and Blink doorbell:
- Sends video to corporate servers you don't control
- Shares footage with law enforcement without your consent ([source](https://www.eff.org/deeplinks/2022/07/ring-reveals-they-give-videos-police-without-user-consent-or-warrant))
- Requires a monthly subscription for basic features
- Stops working if the company goes under or kills the product

OpenBell does **none** of that. Your video never leaves your house.

---

## What You Need

| Item | Cost | Notes |
|------|------|-------|
| Cheap Android phone | ~$50 | TCL 30Z, Moto G Play, any phone with a working camera and WiFi |
| A PC or laptop | (you probably have one) | Linux recommended; runs the server + dashboard |
| USB cable + charger | ~$5 | To keep the phone powered 24/7 |
| WiFi network | (you have one) | Phone and PC must be on the same network |
| *(Optional)* Phone mount/case | ~$10 | Weatherproof mount for outdoor use |
| *(Optional)* WiFi relay (Shelly 1) | ~$12 | To ring your house's existing physical doorbell chime |

**Total: ~$50-75** — no monthly fees, ever.

---

## Setup Guide

### Step 1: Get the Server PC Ready

You need Rust, Node.js, and PipeWire (for audio) on your Linux PC.

```bash
# Install Rust (if you don't have it)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# Install Node.js (if you don't have it — use your package manager)
# Ubuntu/Debian:
sudo apt install nodejs npm
# Arch:
sudo pacman -S nodejs npm

# Install PipeWire utilities (for doorbell chime + intercom audio)
# Ubuntu/Debian:
sudo apt install pipewire pipewire-pulse pipewire-audio-client-libraries
# Arch:
sudo pacman -S pipewire pipewire-pulse
```

### Step 2: Clone and Build

```bash
git clone https://github.com/mwillis775/OpenBell.git
cd OpenBell

# Build the Rust server
cd rust-server
cargo build
cd ..

# Install Electron dashboard dependencies
cd electron-app
npm install
cd ..
```

### Step 3: Install the App on the Phone

You need Android Studio's `adb` tool, or just Android SDK platform-tools:

```bash
# Download platform-tools if needed:
# https://developer.android.com/tools/releases/platform-tools

# On the phone:
# 1. Go to Settings → About Phone → tap "Build Number" 7 times to enable Developer Options
# 2. Go to Settings → Developer Options → turn on "USB Debugging"
# 3. Plug the phone into your PC via USB

# Build and install the app
cd android-app
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### Step 4: Configure the Phone App

Before the app locks into fullscreen mode, you need to set the server URL. The easiest way:

```bash
# Set the server IP directly via adb (replace with YOUR PC's IP)
adb shell "am start -n com.doorbell.app/.ui.SettingsActivity"
```

In the Settings screen:
- **Server URL**: `http://<YOUR_PC_IP>:5000` (find your IP with `hostname -I | awk '{print $1}'`)
- **Device Name**: Whatever you like (e.g., "Front Door")
- **Stream Port**: `8080` (default is fine)

To find your PC's IP:
```bash
hostname -I | awk '{print $1}'
```

### Step 5: Start Everything

```bash
./start.sh
```

That's it. The server starts, the dashboard opens, and the phone connects automatically.

### Step 6: Mount the Phone

- Mount the phone at your front door with a weatherproof case
- Plug it into USB power so it stays on 24/7
- The app keeps the screen on and runs in fullscreen kiosk mode
- The screen shows a clock plus a big bell button — that's all visitors see

---

## How It Works

```
┌─────────────────────┐                              ┌─────────────────────┐
│   ANDROID PHONE     │         Your WiFi            │   YOUR PC           │
│   (at front door)   │         (LAN only)           │                     │
│                     │                              │                     │
│  Camera ──MJPEG────────────── port 8080 ────────►  │  Electron Dashboard │
│                     │                              │  (view camera feed, │
│  Bell button ──WebSocket───── port 5000 ────────►  │   answer calls,     │
│                     │                              │   push-to-talk)     │
│  Speaker ◄──UDP──────────── port 5002 ◄──────────  │                     │
│  (intercom)         │                              │  Rust Server        │
│                     │                              │  (coordinates       │
│  Microphone ──UDP───────── port 5003 ──────────►   │   everything)       │
│  (always-on audio)  │                              │                     │
│                     │                              │  🔔 Ding-dong chime │
│                     │                              │  (through speakers) │
└─────────────────────┘                              └─────────────────────┘
```

**All traffic stays on your local network.** The Electron app actively blocks any attempt to reach the internet. The Rust server binds only to local interfaces. There are zero external API calls.

---

## Optional: Ring Your Real Doorbell

If your house has a wired doorbell chime (the kind with a transformer), you can make OpenBell ring it too by adding a ~$12 WiFi relay:

1. Buy a [Shelly 1 Mini](https://www.shelly.com/en/products/shelly-1-mini-gen3) (~$12) or any WiFi relay
2. Wire its N.O. (normally open) contacts in parallel with your existing doorbell button
3. Set the relay URL when starting:

```bash
DOORBELL_RELAY_URL="http://<RELAY_IP>/relay/0?turn=on&timer=1" ./start.sh
```

Now the physical chime rings AND the PC plays the digital ding-dong when someone presses the button.

---

## Privacy & Security

This section isn't marketing — it's a technical guarantee you can verify by reading the source code.

### What OpenBell does NOT do:
- **No internet access** — The Electron dashboard has a built-in network firewall that blocks all non-LAN requests
- **No cloud anything** — No accounts, no servers, no "optional telemetry," no analytics
- **No facial recognition** — No face data is collected, stored, or transmitted
- **No data collection** — No logs are shipped anywhere. Everything stays in your house.
- **No phone-home** — Even the font is bundled locally instead of loaded from Google Fonts

### What OpenBell DOES do:
- Streams video over your **local WiFi only** (MJPEG over HTTP, LAN addresses only)
- Sends audio over **local UDP only** (ports 5002/5003, LAN addresses only)
- Coordinates via **local WebSocket only** (port 5000, localhost + LAN)
- Advertises via **mDNS** (multicast DNS on 224.0.0.251 — link-local, routers don't forward it)

### How to verify yourself:
```bash
# Watch all network traffic leaving your machine while OpenBell runs:
sudo tcpdump -i any 'not (net 192.168.0.0/16 or net 10.0.0.0/8 or net 172.16.0.0/12 or net 127.0.0.0/8 or net 224.0.0.0/4)' -c 100

# If this shows ZERO packets — nothing is leaving your network.
```

---

## Recommended Phones

Any cheap Android phone with a camera works. Tested on:

| Phone | Price | Notes |
|-------|-------|-------|
| **TCL 30Z** | ~$35-50 | Great value, decent camera, works perfectly |
| Moto G Play | ~$50-70 | Reliable, good battery backup if power goes out |
| Samsung Galaxy A03 | ~$40-60 | Solid option |
| Any old phone you have | $0 | As long as it has Android 8+ and WiFi |

The phone doesn't need cell service. WiFi only is fine.

---

## Environment Variables

All configuration is done through environment variables. Copy `.env.example` to `.env` and edit as needed — `start.sh` sources it automatically.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENBELL_PORT` | `5000` | Rust server listen port |
| `OPENBELL_SERVER_URL` | `http://localhost:5000` | Server URL (used by CV server, voice assistant, Android build) |
| `OPENBELL_WS_URL` | `ws://localhost:5000/ws` | WebSocket URL (used by Electron dashboard, voice assistant) |
| `DOORBELL_RELAY_URL` | *(unset — relay disabled)* | WiFi relay HTTP endpoint (e.g. `http://<RELAY_IP>/relay/0?turn=on&timer=1`) |
| `OPENBELL_RUST_HOST` | `127.0.0.1` | Host address of the Rust server for inter-process UDP |
| `OPENBELL_ASSISTANT_LISTEN_PORT` | `5004` | UDP port: Rust → voice assistant audio |
| `OPENBELL_ASSISTANT_SEND_PORT` | `5005` | UDP port: voice assistant → Rust TTS audio |
| `OPENBELL_ASSISTANT_LISTEN` | `127.0.0.1:5004` | Full address Rust uses to forward mic audio to assistant |
| `OPENBELL_YOLO_MODEL` | `yolov8s.pt` | Path to YOLO model weights |
| `OPENBELL_DEVICE` | `cuda` | Inference device (`cpu`, `cuda`, `cuda:0`) |
| `OPENBELL_WHISPER_MODEL` | `base` | Whisper STT model size |
| `OPENBELL_WHISPER_DEVICE` | `cpu` | Whisper inference device |
| `OPENBELL_PIPER_VOICE` | `en_GB-jenny_dioco-medium` | Piper TTS voice |
| `OPENBELL_AUTO_ANSWER_SECS` | `5` | Seconds before voice assistant auto-answers |

See `.env.example` for the full list.

---

## Project Structure

```
OpenBell/
├── start.sh                # One-command startup script
├── rust-server/            # Rust coordination server
│   ├── src/main.rs         # Entry point
│   ├── src/ws_server.rs    # WebSocket + REST API
│   ├── src/audio.rs        # PipeWire audio pipelines + doorbell chime
│   ├── src/relay.rs        # Physical doorbell relay trigger
│   ├── src/protocol.rs     # Message types
│   ├── src/state.rs        # Shared state
│   └── assets/doorbell.wav # Ding-dong chime sound
├── electron-app/           # Desktop dashboard (Electron)
│   ├── src/main.js         # Main process + network firewall
│   ├── src/renderer.js     # WebSocket client + UI logic
│   ├── src/styles.css      # Phosphor green terminal theme
│   └── src/index.html      # Dashboard layout
├── android-app/            # Phone doorbell app (Kotlin)
│   └── app/src/main/
├── LICENSE                 # MIT License
└── README.md               # You are here
```

---

## Troubleshooting

**Phone won't connect to server:**
- Make sure both devices are on the same WiFi network
- Check the server URL in the phone app settings matches your PC's IP
- Run `hostname -I` on the PC to confirm the IP

**No doorbell chime sound:**
- Make sure PipeWire is running: `wpctl status`
- Check that `pw-play` is installed: `which pw-play`
- The chime targets HDMI speakers by default — edit `DOORBELL_SINK` in `audio.rs` to match your output device

**No audio / intercom not working:**
- Confirm `pw-cat` is installed: `which pw-cat`
- Check PipeWire is the active audio server (not plain ALSA)

**Dashboard shows "Disconnected":**
- Is the Rust server running? Check: `ss -tlnp | grep 5000`
- Start the server first, then the dashboard

**Camera feed not showing:**
- The phone streams MJPEG on port 8080 — make sure that port isn't blocked
- Grant camera permission when the app asks on first launch

---

## License

[MIT](LICENSE) — do whatever you want with it.

---

*Built because renting your own front door camera from Amazon shouldn't be the only option.*
