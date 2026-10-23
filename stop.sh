#!/usr/bin/env bash
# Stop the AI workplace gracefully (workers finish current step, leases expire).
set -euo pipefail
cd "$(dirname "$0")"
if [ -f workplace.pid ]; then
  kill "$(cat workplace.pid)" 2>/dev/null || true
  rm -f workplace.pid
  echo "Stopped."
else
  pkill -f "backend.app.main:app" && echo "Stopped." || echo "Not running."
fi
