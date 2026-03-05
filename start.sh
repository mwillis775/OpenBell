#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  OpenBell — Start everything
# ═══════════════════════════════════════════════════════════
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
G='\033[0;32m' R='\033[0;31m' Y='\033[0;33m' N='\033[0m'

cleanup() {
  echo -e "\n${Y}Shutting down OpenBell...${N}"
  kill "$SERVER_PID" 2>/dev/null && echo "  Server stopped" || true
  kill "$ELECTRON_PID" 2>/dev/null && echo "  Dashboard stopped" || true
  # Clean up audio pipelines spawned by the server
  pkill -f "pw-cat.*doorbell" 2>/dev/null || true
  echo -e "${G}Done.${N}"
}
trap cleanup EXIT INT TERM

# ── Kill any leftovers ──
pkill -f doorbell-server 2>/dev/null || true
fuser -k -n tcp 5000 2>/dev/null || true
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

echo -e "\n${G}OpenBell is running. Press Ctrl+C to stop.${N}\n"

# Keep alive — wait for either process to exit
wait -n "$SERVER_PID" "$ELECTRON_PID" 2>/dev/null || true
