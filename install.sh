#!/usr/bin/env bash
# =============================================================================
# install.sh — Solar Monitor installation script
#
# Run as root (sudo) on a Raspberry Pi running Raspberry Pi OS (Debian-based).
#
# What this script does:
#   1. Checks prerequisites (Python 3.9+, pip, git)
#   2. Creates a dedicated 'solar' system user
#   3. Copies the application to /opt/solar_monitor
#   4. Creates a Python virtual environment and installs dependencies
#   5. Creates the data/ and logs/ directories with correct permissions
#   6. Initialises the SQLite database
#   7. Installs and enables the systemd service
#
# Usage:
#   sudo bash install.sh
#
# Re-running this script is safe — it will update an existing installation.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_USER="solar"
INSTALL_DIR="/opt/solar_monitor"
SERVICE_FILE="solar-monitor.service"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. Must run as root
# ---------------------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
  error "This script must be run as root. Try: sudo bash install.sh"
fi

info "Solar Monitor installer starting"
info "Source directory : $SOURCE_DIR"
info "Install directory: $INSTALL_DIR"

# ---------------------------------------------------------------------------
# 1. Check prerequisites
# ---------------------------------------------------------------------------

info "Checking prerequisites…"

command -v python3 >/dev/null 2>&1 || error "python3 not found. Install with: sudo apt install python3"

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ $PYTHON_MAJOR -lt 3 || ($PYTHON_MAJOR -eq 3 && $PYTHON_MINOR -lt 9) ]]; then
  error "Python 3.9+ required (found $PYTHON_VERSION). Update Python and retry."
fi
info "Python $PYTHON_VERSION — OK"

# Ensure pip and venv are available
python3 -m pip --version >/dev/null 2>&1 || {
  info "pip not found — installing…"
  apt-get install -y python3-pip
}

python3 -m venv --help >/dev/null 2>&1 || {
  info "venv not found — installing…"
  apt-get install -y python3-venv
}

# ---------------------------------------------------------------------------
# 2. Create system user
# ---------------------------------------------------------------------------

if id "$APP_USER" &>/dev/null; then
  info "User '$APP_USER' already exists — skipping creation"
else
  info "Creating system user '$APP_USER'…"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
  info "User '$APP_USER' created"
fi

# ---------------------------------------------------------------------------
# 3. Copy application files
# ---------------------------------------------------------------------------

info "Installing application files to $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"

# Copy the solar_monitor package and all deployment files
rsync -av --delete \
  "$SOURCE_DIR/solar_monitor/" "$INSTALL_DIR/solar_monitor/" \
  --exclude '__pycache__' --exclude '*.pyc'

# Copy requirements.txt alongside the package
cp "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 4. Python virtual environment and dependencies
# ---------------------------------------------------------------------------

VENV_DIR="$INSTALL_DIR/venv"

if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating Python virtual environment…"
  python3 -m venv "$VENV_DIR"
fi

info "Installing Python dependencies (this may take a few minutes on a Pi)…"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
info "Dependencies installed"

# ---------------------------------------------------------------------------
# 5. Create data and log directories
# ---------------------------------------------------------------------------

info "Creating data and log directories…"
mkdir -p "$INSTALL_DIR/data"
mkdir -p "$INSTALL_DIR/logs"

# ---------------------------------------------------------------------------
# 6. Set ownership and permissions
# ---------------------------------------------------------------------------

info "Setting file ownership and permissions…"
chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
chmod -R 750 "$INSTALL_DIR"
# data/ and logs/ need write access for the running service
chmod 770 "$INSTALL_DIR/data"
chmod 770 "$INSTALL_DIR/logs"

# ---------------------------------------------------------------------------
# 7. Install and enable the systemd service
# ---------------------------------------------------------------------------

info "Installing systemd service…"
cp "$SOURCE_DIR/$SERVICE_FILE" "/etc/systemd/system/$SERVICE_FILE"
systemctl daemon-reload
systemctl enable "$SERVICE_FILE"
info "Service enabled — will start automatically on boot"

# ---------------------------------------------------------------------------
# 8. Start (or restart) the service
# ---------------------------------------------------------------------------

if systemctl is-active --quiet "$SERVICE_FILE"; then
  info "Restarting solar-monitor service…"
  systemctl restart "$SERVICE_FILE"
else
  info "Starting solar-monitor service…"
  systemctl start "$SERVICE_FILE"
fi

sleep 2

if systemctl is-active --quiet "$SERVICE_FILE"; then
  info "Service is running"
else
  warn "Service may not have started correctly. Check: sudo journalctl -u solar-monitor -n 50"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN} Solar Monitor installed successfully!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  Dashboard URL : http://$(hostname -I | awk '{print $1}'):8000"
echo "  Service status: sudo systemctl status solar-monitor"
echo "  Live logs     : sudo journalctl -u solar-monitor -f"
echo ""
echo "  Before the service will work, edit config.py and set the"
echo "  Classic 200 IP address:"
echo "    sudo nano $INSTALL_DIR/solar_monitor/config.py"
echo "  Then restart:   sudo systemctl restart solar-monitor"
echo ""
