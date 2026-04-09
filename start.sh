#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  OpenBell — Start everything
# ═══════════════════════════════════════════════════════════
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

# Source .env if present (user-specific configuration)
if [[ -f "$DIR/.env" ]]; then
  set -a
  source "$DIR/.env"
  set +a
fi

# Colors
G='\033[0;32m' R='\033[0;31m' Y='\033[0;33m' N='\033[0m'

cleanup() {
  echo -e "\n${Y}Shutting down OpenBell...${N}"
  kill "$SERVER_PID" 2>/dev/null && echo "  Server stopped" || true
  [[ -n "$CV_PID" ]] && kill "$CV_PID" 2>/dev/null && echo "  CV server stopped" || true
  [[ -n "$VA_PID" ]] && kill "$VA_PID" 2>/dev/null && echo "  Voice assistant stopped" || true
  kill "$ELECTRON_PID" 2>/dev/null && echo "  Dashboard stopped" || true
  # Clean up audio pipelines spawned by the server
  pkill -f "pw-cat.*doorbell" 2>/dev/null || true
  echo -e "${G}Done.${N}"
}
trap cleanup EXIT INT TERM

CV_PID=""
VA_PID=""

# ── Kill any leftovers ──
pkill -f doorbell-server 2>/dev/null || true
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

# ── Start Rust server ──
echo -e "${Y}Starting server...${N}"
cd "$DIR/rust-server"
RUST_LOG=doorbell_server=info cargo run --bin doorbell-server 2>&1 &
SERVER_PID=$!

# Wait for server to bind
for i in $(seq 1 20); do
  if ss -tlnp 2>/dev/null | grep -q ':5000'; then
    echo -e "${G}  Server ready on :5000${N}"
    break
  fi
  sleep 0.5
done

# ── Start Electron dashboard ──
echo -e "${Y}Starting dashboard...${N}"
cd "$DIR/electron-app"
npm start 2>&1 &
ELECTRON_PID=$!
echo -e "${G}  Dashboard launched${N}"

# ── Start CV server (YOLOv8 person detection) ──
echo -e "${Y}Starting CV server...${N}"
cd "$DIR/cv-server"
if [[ ! -d "venv" ]]; then
  echo -e "${Y}  Creating Python venv...${N}"
  python3 -m venv venv
fi
venv/bin/pip install -q -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
venv/bin/python main.py 2>&1 &
CV_PID=$!
echo -e "${G}  CV server launched (PID $CV_PID)${N}"

# ── Start Voice Assistant (Whisper + Piper TTS) ──
echo -e "${Y}Starting voice assistant...${N}"
cd "$DIR/voice-assistant"
if [[ ! -d "venv" ]]; then
  echo -e "${Y}  Creating Python venv...${N}"
  python3 -m venv venv
fi
venv/bin/pip install -q -r requirements.txt
venv/bin/python main.py 2>&1 &
VA_PID=$!
echo -e "${G}  Voice assistant launched (PID $VA_PID)${N}"

echo -e "\n${G}OpenBell is running. Press Ctrl+C to stop.${N}\n"

# Keep alive — wait for any process to exit
wait -n "$SERVER_PID" "$CV_PID" "$VA_PID" "$ELECTRON_PID" 2>/dev/null || true
