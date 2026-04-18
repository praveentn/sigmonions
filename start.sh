#!/usr/bin/env bash
# ============================================================
#  Sigmonions Discord Bot — Unix/Mac startup script
#  Usage:
#    ./start.sh            (reads PORT from .env, default 8080)
#    PORT=9090 ./start.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  =========================================="
echo "    🎮  Sigmonions Discord Bot"
echo "  =========================================="
echo ""

# ── Read PORT from .env if not already set ────────────────────
if [ -z "${PORT:-}" ] && [ -f ".env" ]; then
    PORT=$(grep -E '^PORT=' .env | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ') || true
fi
PORT="${PORT:-8080}"
echo "  [sigmonions] Port   : $PORT"

# ── Detect python command ─────────────────────────────────────
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null && "$cmd" -c "import sys; assert sys.version_info >= (3,10)" 2>/dev/null; then
        PYTHON_CMD="$cmd"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "  [sigmonions] ERROR: Python 3.10+ not found in PATH."
    echo "  Install from https://python.org"
    exit 1
fi
echo "  [sigmonions] Python : $($PYTHON_CMD --version)"

# ── Check / free the port ─────────────────────────────────────
if lsof -Pi ":$PORT" -sTCP:LISTEN -t &>/dev/null; then
    PID=$(lsof -ti ":$PORT" -sTCP:LISTEN)
    echo "  [sigmonions] WARNING: Port $PORT in use by PID $PID — killing..."
    kill "$PID" && sleep 1
    echo "  [sigmonions] OK: Port $PORT freed."
else
    echo "  [sigmonions] OK: Port $PORT is free."
fi

# ── Virtual environment ───────────────────────────────────────
if [ ! -f "venv/bin/activate" ]; then
    echo "  [sigmonions] Creating virtual environment..."
    "$PYTHON_CMD" -m venv venv
    echo "  [sigmonions] OK: venv created."
fi

source venv/bin/activate
echo "  [sigmonions] venv   : $(python --version)"

# ── Install / sync requirements ───────────────────────────────
echo ""
echo "  [sigmonions] Checking requirements..."
pip install -r requirements.txt -q --disable-pip-version-check
echo "  [sigmonions] OK: Dependencies up to date."

# ── Token check (non-fatal) ───────────────────────────────────
echo ""
TOKEN_VAL=""
if [ -f ".env" ]; then
    TOKEN_VAL=$(grep -E '^DISCORD_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ') || true
fi

if [ -z "$TOKEN_VAL" ] || [ "$TOKEN_VAL" = "your_bot_token_here" ]; then
    echo "  [sigmonions] WARNING: DISCORD_TOKEN is not set in .env"
    echo "  [sigmonions]          Bot will start in local-only mode."
    echo "  [sigmonions]          Open http://localhost:$PORT/ for setup instructions."
else
    echo "  [sigmonions] OK: Discord token found."
fi

# ── Launch ────────────────────────────────────────────────────
echo ""
echo "  [sigmonions] Starting bot  -->  http://localhost:$PORT/"
echo "  [sigmonions] Press Ctrl+C to stop."
echo ""

PORT="$PORT" python bot.py
