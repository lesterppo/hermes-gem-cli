#!/bin/bash
# install.sh — one-command install for hermes-gem-cli
set -e

echo "=== hermes-gem-cli installer ==="
echo ""

# Check Python
PYTHON=$(which python3 || which python)
echo "[1/3] Python: $($PYTHON --version)"

# Install deps
echo "[2/3] Installing dependencies..."
$PYTHON -m pip install --break-system-packages gemini-webapi browser-cookie3 loguru 2>/dev/null || \
$PYTHON -m pip install gemini-webapi browser-cookie3 loguru

# Install gem-cli
INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"
cp gem-cli.py "$INSTALL_DIR/gem-cli"
chmod +x "$INSTALL_DIR/gem-cli"

echo "[3/3] Installed to $INSTALL_DIR/gem-cli"
echo ""

# Check if in PATH
if echo "$PATH" | grep -q "$INSTALL_DIR"; then
    echo "✓ $INSTALL_DIR is in PATH"
else
    echo "! Add $INSTALL_DIR to your PATH:"
    echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
fi

echo ""
echo "=== Next steps ==="
echo "  1. Sign in at https://gemini.google.com in Firefox or Chrome"
echo "  2. Run: gem-cli --init     (cache auth tokens)"
echo "  3. Run: gem-cli --help     (see all options)"
echo "  4. Try:  gem-cli '<shared-gem-url>' 'hello'  -m flash --brief"
