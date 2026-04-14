#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
#  Lumi — AI Document Intelligence
#  Startup script for macOS / Linux
#  Usage: ./start.sh
# ─────────────────────────────────────────────────────

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
LUMI_PATH="$DIR/lumi_project"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Lumi — AI Document Intelligence            ║"
echo "║   AWS Textract + Amazon Bedrock Nova Lite    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Create virtualenv if not exists
if [ ! -d "$VENV" ]; then
  echo "→ Creating virtual environment..."
  python3 -m venv "$VENV"
fi

# Activate
source "$VENV/bin/activate"

# Install dependencies
echo "→ Installing dependencies..."
pip install -r "$DIR/requirements.txt" -q

# Add lumi path to PYTHONPATH
export PYTHONPATH="$LUMI_PATH:$PYTHONPATH"

# Copy .env if exists in lumi project
if [ -f "$LUMI_PATH/.env" ] && [ ! -f "$DIR/.env" ]; then
  cp "$LUMI_PATH/.env" "$DIR/.env"
fi

echo "→ Starting Lumi server..."
echo "→ Open http://localhost:5000 in your browser"
echo ""

cd "$DIR"
python server.py
