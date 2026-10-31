#!/usr/bin/env bash
# Auto-Remove-Manager one-command installer
# SPDX-License-Identifier: MIT
set -Eeuo pipefail
IFS=$'\n\t'

APP_NAME="Auto-Remove-Manager"
MODE=""
YES=0
REPO_URL="https://github.com/race-in/Auto-Remove-Manager.git"
MARKER="AUTO-REMOVE-MANAGER-OWNED-V1"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
info(){ printf '%s➜ %s%s\n' "$CYAN" "$*" "$RESET"; }
ok(){ printf '%s✓ %s%s\n' "$GREEN" "$*" "$RESET"; }
warn(){ printf '%s! %s%s\n' "$YELLOW" "$*" "$RESET"; }
die(){ printf '%s✗ %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
has(){ command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
Auto-Remove-Manager installer

Usage:
  bash install.sh [--mode root|rootless] [--yes]
  bash install.sh --help

root      System-wide install (must run as root).
rootless  Per-user install (must run as the target user, without sudo).
No mode  Interactive selection.

The initial setup is preview-only. It never enables torrent or payload deletion.
EOF
}

while (($#)); do
  case "$1" in
    --mode) (($# >= 2)) || die "--mode requires root or rootless"; MODE="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

if [[ -z "$MODE" ]]; then
  [[ -t 0 ]] || die "Use --mode root or --mode rootless for non-interactive installation."
  printf '\n%s\n  1) Root / system-wide\n  2) Rootless / per-user\n' "$APP_NAME"
  read -r -p "Select mode [1-2]: " choice
  case "$choice" in 1) MODE=root ;; 2) MODE=rootless ;; *) die "Invalid selection." ;; esac
fi
[[ "$MODE" == root || "$MODE" == rootless ]] || die "Mode must be root or rootless."

if [[ "$MODE" == root ]]; then
  [[ "$EUID" -eq 0 ]] || die "Root mode requires root. Use sudo bash install.sh --mode root."
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    RUN_USER="$SUDO_USER"
  else
    RUN_USER="root"
  fi
  RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
  [[ -n "$RUN_HOME" && -d "$RUN_HOME" ]] || die "Could not resolve home directory for $RUN_USER."
  BASE="/opt/auto-remove-manager"
  CONFIG_DIR="/etc/auto-remove-manager"
  STATE_DIR="/var/lib/auto-remove-manager"
  LOG_DIR="/var/log/auto-remove-manager"
  BIN_DIR="/usr/local/bin"
  UNIT_DIR="/etc/systemd/system"
else
  [[ "$EUID" -ne 0 ]] || die "Rootless mode must run as a normal user. Do not use sudo."
  RUN_USER="$(id -un)"
  RUN_HOME="$HOME"
  BASE="$RUN_HOME/.local/share/auto-remove-manager"
  CONFIG_DIR="$RUN_HOME/.config/auto-remove-manager"
  STATE_DIR="$RUN_HOME/.local/state/auto-remove-manager"
  LOG_DIR="$STATE_DIR/logs"
  BIN_DIR="$RUN_HOME/.local/bin"
  UNIT_DIR="$RUN_HOME/.config/systemd/user"
fi

if ! has python3 || ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3,8))'; then
  if [[ "$MODE" == root && -f /etc/debian_version && has apt-get ]]; then
    info "Installing required Debian/Ubuntu packages (no system upgrade)."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates git python3 python3-venv
  else
    die "Python 3.8+ and python3-venv are required. Install them, then rerun."
  fi
fi
has git || {
  if [[ "$MODE" == root && -f /etc/debian_version && has apt-get ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends git ca-certificates
  else
    die "git is required. Install it, then rerun."
  fi
}
python3 -m venv --help >/dev/null 2>&1 || die "python3-venv is required."

mkdir -p "$(dirname "$BASE")"
if [[ -e "$BASE" ]]; then
  [[ -d "$BASE/.git" ]] || die "Refusing to overwrite non-Git path: $BASE"
  if ((YES == 0)); then
    read -r -p "Existing Auto-Remove-Manager installation found. Update it? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || exit 0
  fi
  git -C "$BASE" fetch --quiet origin
  git -C "$BASE" reset --quiet --hard origin/main
else
  git clone --quiet --depth 1 "$REPO_URL" "$BASE"
fi

mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR" "$BIN_DIR" "$UNIT_DIR"
for dir in "$BASE" "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR"; do
  [[ ! -L "$dir" ]] || die "Refusing symlinked managed directory: $dir"
  printf '%s\n' "$MARKER" > "$dir/.arm-owned"
  chmod 700 "$dir"
done

VENV="$BASE/venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --disable-pip-version-check "autoremove-torrents==1.5.5" PyYAML requests

if [[ ! -f "$CONFIG_DIR/manager.json" ]]; then
  "$VENV/bin/python" - "$CONFIG_DIR/manager.json" "$MODE" <<'PY'
import json, os, sys, tempfile
path, mode = sys.argv[1:]
data = {
    "schema_version": 1,
    "mode": mode,
    "preview_only": True,
    "allow_removal": False,
    "allow_data_deletion": False,
    "schedule": {"enabled": True, "interval_minutes": 60}
}
fd, tmp = tempfile.mkstemp(prefix=".manager-", dir=os.path.dirname(path), text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
fi

if [[ ! -f "$CONFIG_DIR/config.yml" ]]; then
  printf '{}\n' > "$CONFIG_DIR/config.yml"
  chmod 600 "$CONFIG_DIR/config.yml"
fi

cat > "$BIN_DIR/autoremove-manager" <<EOF
#!/usr/bin/env bash
# $MARKER
set -Eeuo pipefail
export PATH="$VENV/bin:$BIN_DIR:/usr/local/bin:/usr/bin:/bin"
exec "$VENV/bin/python" "$BASE/src/manager.py" \
  --mode "$MODE" \
  --config "$CONFIG_DIR/manager.json" \
  --upstream-config "$CONFIG_DIR/config.yml" \
  --state-dir "$STATE_DIR" \
  --log-dir "$LOG_DIR" "\$@"
EOF
chmod 755 "$BIN_DIR/autoremove-manager"

if [[ "$MODE" == root ]]; then
  chown -R "$RUN_USER":"$(id -gn "$RUN_USER")" "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR"
  chown "$RUN_USER":"$(id -gn "$RUN_USER")" "$BIN_DIR/autoremove-manager"
  cat > "$UNIT_DIR/auto-remove-manager.service" <<EOF
# $MARKER
[Unit]
Description=Auto-Remove-Manager scheduled preview
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$(id -gn "$RUN_USER")
Environment=HOME=$RUN_HOME
ExecStart=$BIN_DIR/autoremove-manager scheduled
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$CONFIG_DIR $STATE_DIR $LOG_DIR
EOF
  cat > "$UNIT_DIR/auto-remove-manager.timer" <<'EOF'
# AUTO-REMOVE-MANAGER-OWNED-V1
[Unit]
Description=Run Auto-Remove-Manager every hour

[Timer]
OnBootSec=10m
OnUnitActiveSec=60m
Persistent=true
Unit=auto-remove-manager.service

[Install]
WantedBy=timers.target
EOF
  if has systemctl; then
    systemctl daemon-reload
    systemctl enable --now auto-remove-manager.timer
    ok "System timer enabled (preview mode)."
  else
    warn "systemd is unavailable; schedule the preview command with root's crontab."
  fi
else
  chown -R "$RUN_USER":"$(id -gn "$RUN_USER")" "$BASE" "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR" "$BIN_DIR"
  cat > "$UNIT_DIR/auto-remove-manager.service" <<EOF
# $MARKER
[Unit]
Description=Auto-Remove-Manager scheduled preview
After=network-online.target

[Service]
Type=oneshot
Environment=HOME=$RUN_HOME
ExecStart=$BIN_DIR/autoremove-manager scheduled
NoNewPrivileges=true
PrivateTmp=true
EOF
  cat > "$UNIT_DIR/auto-remove-manager.timer" <<'EOF'
# AUTO-REMOVE-MANAGER-OWNED-V1
[Unit]
Description=Run Auto-Remove-Manager every hour

[Timer]
OnBootSec=10m
OnUnitActiveSec=60m
Persistent=true
Unit=auto-remove-manager.service

[Install]
WantedBy=default.target
EOF
  if has systemctl && systemctl --user show-environment >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable --now auto-remove-manager.timer
    ok "User systemd timer enabled (preview mode)."
  elif has crontab; then
    (crontab -l 2>/dev/null | grep -v '# AUTO-REMOVE-MANAGER'; printf '0 * * * * %s scheduled # AUTO-REMOVE-MANAGER\n' "$BIN_DIR/autoremove-manager") | crontab -
    warn "User systemd is unavailable; installed a per-user hourly preview cron job."
  else
    warn "No user systemd or crontab found; scheduler is not enabled."
  fi
fi

ok "$APP_NAME installed in $MODE mode."
info "Run: $BIN_DIR/autoremove-manager detect"
info "Configure clients: $BIN_DIR/autoremove-manager configure"
info "Review proposed removals: $BIN_DIR/autoremove-manager preview"
warn "Actual torrent removal is disabled. Downloaded-data deletion is also disabled."
warn "Uninstall with: $BIN_DIR/autoremove-manager uninstall"
