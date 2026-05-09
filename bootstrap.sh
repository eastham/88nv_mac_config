#!/bin/bash
# 88NV ATC Mac Mini Bootstrap
#
# Run this FIRST on a freshly imaged Mac Mini, before setup.py.
# It installs Xcode Command Line Tools (which includes git and python3),
# clones this config repo, and installs the one Python dep needed for setup.py.
#
# Usage:
#   curl -O https://raw.githubusercontent.com/eastham/88nv_mac_config/main/bootstrap.sh
# easier-to-type location:
#   curl -O https://airbornehotspots.org/bootstrap.sh
#
# Then:
#   bash bootstrap.sh
#
# After this completes:
#   1. Edit ~/git-mac/88nv_mac_config/config.md with this machine' hostname
#   2. python3 ~/git-mac/88nv_mac_config/setup.py

REPO_URL="https://github.com/eastham/88nv_mac_config"
CLONE_DIR="$HOME/git-mac/88nv_mac_config"

set -e  # Exit on error within bootstrap (setup.py handles per-step errors)

echo ""
echo "========================================================"
echo "  88NV Mac Mini Bootstrap"
echo "========================================================"
echo ""

# -------------------------------------------------------
# Step 1: Xcode Command Line Tools
# -------------------------------------------------------
echo "=== [xcode] Checking Xcode Command Line Tools ==="
if xcode-select -p &>/dev/null; then
    echo "=== [xcode] Already installed at: $(xcode-select -p)"
else
    echo "=== [xcode] Not installed — launching installer..."
    echo "    A dialog box may appear. Click 'Install' to continue."
    xcode-select --install

    echo "=== [xcode] Waiting for installation to complete (checking every 10s)..."
    until xcode-select -p &>/dev/null; do
        sleep 10
        echo "    Still waiting..."
    done
    echo "=== [xcode] Xcode Command Line Tools installed at: $(xcode-select -p)"
fi

# -------------------------------------------------------
# Step 2: Clone this config repo
# -------------------------------------------------------
echo ""
echo "=== [clone] Cloning 88nv_mac_config repo ==="
mkdir -p "$HOME/git-mac"
if [ -d "$CLONE_DIR/.git" ]; then
    echo "=== [clone] Repo already exists — pulling latest changes..."
    git -C "$CLONE_DIR" pull
else
    echo "=== [clone] Cloning from $REPO_URL..."
    git clone "$REPO_URL" "$CLONE_DIR"
fi
echo "=== [clone] Repo ready at $CLONE_DIR"

# -------------------------------------------------------
# Step 3: Install markdown-it-py (needed by setup.py to parse config.md)
# -------------------------------------------------------
echo ""
echo "=== [pip] Installing markdown-it-py ==="
python3 -m pip install --user --break-system-packages markdown-it-py
echo "=== [pip] markdown-it-py installed"

# -------------------------------------------------------
# Done
# -------------------------------------------------------
echo ""
echo "========================================================"
echo "  Bootstrap complete!"
echo "========================================================"
echo ""
echo "Next steps:"
echo "  1. Edit config.md with this machine's settings:"
echo "     open $CLONE_DIR/config.md"
echo ""
echo "  2. Run the main setup script:"
echo "     python3 $CLONE_DIR/setup.py"
echo ""
echo "  For network-only re-configuration (skip software install):"
echo "     python3 $CLONE_DIR/setup.py --skip-install"
echo ""
