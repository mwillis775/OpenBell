#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  OpenBell — Start everything
# ═══════════════════════════════════════════════════════════
DIR="$(cd "$(dirname "$0")" && pwd)"

# Source .env if present (user-specific configuration)
if [[ -f "$DIR/.env" ]]; then
  set -a
  source "$DIR/.env"
  set +a
fi

# Colors
G='\033[0;32m' R='\033[0;31m' Y='\033[0;33m' N='\033[0m'

SERVER_PID=""
TAURI_PID=""
CV_PID=""
VA_PID=""

cleanup() {
  echo -e "\n${Y}Shutting down OpenBell...${N}"
  [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null && echo "  Server stopped" || true
  [[ -n "$CV_PID" ]]     && kill "$CV_PID"     2>/dev/null && echo "  CV server stopped" || true
  [[ -n "$VA_PID" ]]     && kill "$VA_PID"     2>/dev/null && echo "  Voice assistant stopped" || true
  [[ -n "$TAURI_PID" ]]  && kill "$TAURI_PID"  2>/dev/null && echo "  Dashboard stopped" || true
  pkill -f "pw-cat.*doorbell" 2>/dev/null || true
  echo -e "${G}Done.${N}"
}
trap cleanup EXIT INT TERM

# ── Kill any leftovers ──
pkill -f doorbell-server 2>/dev/null || true
pkill -f openbell-dashboard 2>/dev/null || true
pkill -f "python.*cv-server" 2>/dev/null || true
pkill -f "python.*voice-assistant" 2>/dev/null || true
fuser -k -n tcp 5000 2>/dev/null || true
fuser -k -n tcp 5100 2>/dev/null || true
fuser -k -n udp 5002 2>/dev/null || true
fuser -k -n udp 5003 2>/dev/null || true
sleep 0.5

echo -e "${G}═══════════════════════════════════════${N}"
echo -e "${G}  ⟫ OpenBell                           ${N}"
echo -e "${G}═══════════════════════════════════════${N}"

# ── Detect phone & correct LAN IP ──
# Known phone IP (can be overridden via .env)
PHONE_IP="${OPENBELL_PHONE_IP:-192.168.4.42}"

# First try: ADB device list
ADB_DEVICE=$(adb devices 2>/dev/null | awk 'NR>1 && /device$/{print $1; exit}')
if [[ -n "$ADB_DEVICE" ]]; then
  PHONE_IP="${ADB_DEVICE%%:*}"
fi

# Ask the kernel which of our IPs can reach the phone
LOCAL_IP=$(ip route get "$PHONE_IP" 2>/dev/null | grep -oP 'src \K\S+' || true)
# Fallback: try the WiFi interface, then first available IP
if [[ -z "$LOCAL_IP" ]]; then
  LOCAL_IP=$(ip -4 addr show wlo1 2>/dev/null | grep -oP 'inet \K[\d.]+' || true)
fi
if [[ -z "$LOCAL_IP" ]]; then
  LOCAL_IP=$(hostname -I | awk '{print $1}')
fi

export OPENBELL_SERVER_URL="http://${LOCAL_IP}:5000"

# Detect phone's built-in MJPEG camera server for pull-proxy mode
PHONE_STREAM=""
if nc -z -w 1 "$PHONE_IP" 8080 2>/dev/null; then
  PHONE_STREAM="http://${PHONE_IP}:8080/"
fi

echo -e "${G}  PC LAN IP:     ${LOCAL_IP}${N}"
echo -e "${G}  Phone:         ${PHONE_IP}${N}"
echo -e "${G}  Phone stream:  ${PHONE_STREAM:-not detected}${N}"
echo -e "${G}  Server URL:    ${OPENBELL_SERVER_URL}${N}"

# ── Android app — only rebuild & deploy when source changed ──
APK="$DIR/android-app/app/build/outputs/apk/debug/app-debug.apk"
ANDROID_STAMP="$DIR/android-app/app/build/.openbell_build_url"

needs_android_build() {
  # Rebuild if APK doesn't exist
  [[ ! -f "$APK" ]] && return 0
  # Rebuild if the baked server URL changed
  [[ ! -f "$ANDROID_STAMP" ]] && return 0
  [[ "$(cat "$ANDROID_STAMP" 2>/dev/null)" != "$OPENBELL_SERVER_URL" ]] && return 0
  # Rebuild if any source file is newer than the APK
  local newest
  newest=$(find "$DIR/android-app/app/src" -type f -newer "$APK" 2>/dev/null | head -1)
  [[ -n "$newest" ]] && return 0
  newest=$(find "$DIR/android-app/app" -maxdepth 1 -name "build.gradle.kts" -newer "$APK" 2>/dev/null | head -1)
  [[ -n "$newest" ]] && return 0
  return 1
}

if needs_android_build; then
  echo -e "${Y}Building Android app...${N}"
  cd "$DIR/android-app"
  if OPENBELL_SERVER_URL="$OPENBELL_SERVER_URL" ./gradlew assembleDebug -q 2>&1; then
    echo "$OPENBELL_SERVER_URL" > "$ANDROID_STAMP"
    echo -e "${G}  Build complete${N}"
  else
    echo -e "${R}  Android build failed${N}"
  fi
else
  echo -e "${G}  Android app up to date — skipping build${N}"
fi

# Deploy to phone if connected
if [[ -n "$ADB_DEVICE" && -f "$APK" ]]; then
  echo -e "${Y}  Deploying to ${ADB_DEVICE}...${N}"
  adb -s "$ADB_DEVICE" install -r "$APK" 2>&1 | tail -1
  # Clear any stale cached server URL from SharedPreferences
  adb -s "$ADB_DEVICE" shell "run-as com.doorbell.app rm -f /data/data/com.doorbell.app/shared_prefs/doorbell_prefs.xml" 2>/dev/null || true
  adb -s "$ADB_DEVICE" shell am force-stop com.doorbell.app
  adb -s "$ADB_DEVICE" shell am start -n com.doorbell.app/.ui.MainActivity 2>/dev/null
  echo -e "${G}  Phone app deployed and started${N}"
elif [[ -z "$ADB_DEVICE" ]]; then
  echo -e "${R}  No ADB device — skipping deploy${N}"
fi

# ── Build Rust server (if needed) ──
RUST_BIN="$DIR/rust-server/target/release/doorbell-server"
if [[ ! -f "$RUST_BIN" ]] || [[ -n "$(find "$DIR/rust-server/src" -type f -newer "$RUST_BIN" 2>/dev/null | head -1)" ]]; then
  echo -e "${Y}Building Rust server...${N}"
  cd "$DIR/rust-server"
  cargo build --release --bin doorbell-server 2>&1 | tail -5
fi

# ── Build Tauri dashboard (if needed) ──
TAURI_BIN="$DIR/tauri-app/src-tauri/target/release/openbell-dashboard"
if [[ ! -f "$TAURI_BIN" ]] || \
   [[ -n "$(find "$DIR/tauri-app/src" -type f -newer "$TAURI_BIN" 2>/dev/null | head -1)" ]] || \
   [[ -n "$(find "$DIR/tauri-app/src-tauri/src" -type f -newer "$TAURI_BIN" 2>/dev/null | head -1)" ]]; then
  echo -e "${Y}Building Tauri dashboard...${N}"
  cd "$DIR/tauri-app/src-tauri"
  cargo build --release 2>&1 | tail -5
fi

# ── Start Rust server ──
echo -e "${Y}Starting server...${N}"
cd "$DIR/rust-server"
export RUST_LOG=doorbell_server=info
if [[ -n "$PHONE_STREAM" ]]; then
  export PHONE_STREAM_URL="$PHONE_STREAM"
else
  unset PHONE_STREAM_URL 2>/dev/null || true
fi
"$RUST_BIN" 2>&1 &
SERVER_PID=$!

# Wait for server to bind
for i in $(seq 1 20); do
  if ss -tlnp 2>/dev/null | grep -q ':5000'; then
    echo -e "${G}  Server ready on :5000${N}"
    break
  fi
  sleep 0.5
done

# ── Start Tauri dashboard ──
echo -e "${Y}Starting dashboard...${N}"
"$TAURI_BIN" 2>&1 &
TAURI_PID=$!
echo -e "${G}  Dashboard launched${N}"

# ── Start CV server (YOLOv8 person detection) ──
echo -e "${Y}Starting CV server...${N}"
cd "$DIR/cv-server"
if [[ ! -d "venv" ]]; then
  echo -e "${Y}  Creating Python venv...${N}"
  python3 -m venv venv
fi
venv/bin/pip install -q -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124 2>&1
venv/bin/python main.py 2>&1 &
CV_PID=$!
echo -e "${G}  CV server launched${N}"

# ── Start Voice Assistant (Whisper + Piper TTS) ──
echo -e "${Y}Starting voice assistant...${N}"
cd "$DIR/voice-assistant"
if [[ ! -d "venv" ]]; then
  echo -e "${Y}  Creating Python venv...${N}"
  python3 -m venv venv
fi
venv/bin/pip install -q -r requirements.txt 2>&1
venv/bin/python main.py 2>&1 &
VA_PID=$!
echo -e "${G}  Voice assistant launched${N}"

echo -e "\n${G}OpenBell is running. Press Ctrl+C to stop.${N}\n"

# Keep alive — wait for any process to exit
wait -n "$SERVER_PID" "$CV_PID" "$VA_PID" "$TAURI_PID" 2>/dev/null || true
