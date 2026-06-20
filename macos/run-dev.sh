#!/usr/bin/env bash
# Start the Phase 3 dev stack: Python sidecar + native Swift shell.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "==> Exporting tool schemas"
python3 scripts/export_tool_schemas.py

echo "==> Starting sidecar on :8765"
if ! python3 -c "import fastapi" 2>/dev/null; then
  pip install -q -r requirements-sidecar.txt
fi

python3 -m sidecar.server &
SIDECAR_PID=$!
trap 'kill $SIDECAR_PID 2>/dev/null || true' EXIT

for _ in {1..30}; do
  if curl -sf http://127.0.0.1:8765/health >/dev/null; then
    echo "    Sidecar healthy"
    break
  fi
  sleep 0.2
done

echo "==> Building Swift app"
cd macos/Aether
swift build -c debug

echo "==> Launching Aether"
.build/debug/Aether &
APP_PID=$!

wait $APP_PID
