#!/usr/bin/env bash
# Start the AI workplace (backend + workers + UI on :8000).
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; source .env; set +a; fi
export WORKPLACE_DATA_DIR="${WORKPLACE_DATA_DIR:-$PWD/data}"
export WORKER_COUNT="${WORKER_COUNT:-4}"
mkdir -p "$WORKPLACE_DATA_DIR"
if [ -f workplace.pid ] && kill -0 "$(cat workplace.pid)" 2>/dev/null; then
  echo "Already running (pid $(cat workplace.pid))."
  exit 0
fi
nohup python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 \
  >"$WORKPLACE_DATA_DIR/server.log" 2>&1 &
echo $! > workplace.pid
sleep 3
curl -sf http://127.0.0.1:8000/health && echo && echo "Up at http://localhost:8000 (log: $WORKPLACE_DATA_DIR/server.log)"
