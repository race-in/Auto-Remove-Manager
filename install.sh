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
No mode   Interactive selection.

The initial setup is preview-only. It never enables torrent or payload deletion.
EOF
}

install_marked_cron() {
  local target_user="$1"
  local command_path="$2"
  local cron_tmp cron_clean
  cron_tmp="$(mktemp)"
  cron_clean="$(mktemp)"

  if [[ -n "$target_user" ]]; then
    crontab -u "$target_user" -l > "$cron_tmp" 2>/dev/null || true
  else
    crontab -l > "$cron_tmp" 2>/dev/null || true
  fi

  grep -v '# AUTO-REMOVE-MANAGER$' "$cron_tmp" > "$cron_clean" || true
  printf '0 * * * * %s scheduled # AUTO-REMOVE-MANAGER\n' "$command_path" >> "$cron_clean"

  if [[ -n "$target_user" ]]; then
    crontab -u "$target_user" "$cron_clean"
  else
    crontab "$cron_clean"
  fi
  rm -f "$cron_tmp" "$cron_clean"
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
  SUDO_USER_VALUE="$(printenv SUDO_USER || true)"
  if [[ -n "$SUDO_USER_VALUE" && "$SUDO_USER_VALUE" != "root" ]]; then
    RUN_USER="$SUDO_USER_VALUE"
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

install_os_packages() {
  local need_venv="$1"
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
  if has apt-get; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    local packages=(ca-certificates git python3 python3-pip)
    [[ "$need_venv" == yes ]] && packages+=(python3-venv)
    apt-get install -y --no-install-recommends "${packages[@]}"
  elif has dnf; then
    dnf install -y ca-certificates git python3 python3-pip
  elif has yum; then
    yum install -y ca-certificates git python3 python3-pip
  elif has zypper; then
    zypper --non-interactive refresh
    zypper --non-interactive install ca-certificates git python3 python3-pip
  elif has pacman; then
    pacman -Sy --needed --noconfirm ca-certificates git python python-pip
  elif has apk; then
    apk add --no-cache ca-certificates git python3 py3-pip
    if [[ "$need_venv" == yes ]]; then apk add --no-cache py3-virtualenv; fi
  else
    die "No supported package manager found. Install Python 3.8+, pip, venv support, Git, and CA certificates manually."
  fi
}

if [[ "$MODE" == root ]]; then
  need_packages=0
  if ! has python3 || ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3,8))'; then need_packages=1; fi
  has git || need_packages=1
  if ((need_packages)); then
    info "Installing missing runtime dependencies with the detected OS package manager."
    install_os_packages no
  fi
  if ! python3 -c 'import venv' >/dev/null 2>&1; then
    info "Installing Python virtual-environment support."
    install_os_packages yes
  fi
else
  python3 -c 'import sys, venv; raise SystemExit(sys.version_info < (3,8))' >/dev/null 2>&1 ||
    die "Rootless mode needs Python 3.8+ with venv support. Install it using your OS package manager, then rerun."
  has git || die "Git is required in rootless mode. Install Git, then rerun."
fi

python3 -c 'import sys, venv; raise SystemExit(sys.version_info < (3,8))' >/dev/null 2>&1 ||
  die "Python 3.8+ with venv support is required."
has git || die "Git is required."
[[ ! -L "$BASE" ]] || die "Refusing symlinked install path: $BASE"
mkdir -p "$(dirname "$BASE")"
if [[ -e "$BASE" ]]; then
  [[ -d "$BASE/.git" ]] || die "Refusing to overwrite non-Git path: $BASE"
  [[ -f "$BASE/.arm-owned" && "$(cat "$BASE/.arm-owned")" == "$MARKER" ]] || die "Refusing to adopt an existing unmarked directory: $BASE"
  if ((YES == 0)); then
    read -r -p "Existing Auto-Remove-Manager installation found. Update it? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || exit 0
  fi
  git -C "$BASE" fetch --quiet origin
  git -C "$BASE" reset --quiet --hard origin/main
else
  git clone --quiet --depth 1 "$REPO_URL" "$BASE"
  printf '%s\n' "$MARKER" > "$BASE/.arm-owned"
  chmod 700 "$BASE"
fi

ensure_owned_dir() {
  local dir="$1"
  [[ ! -L "$dir" ]] || die "Refusing symlinked managed directory: $dir"
  if [[ -e "$dir" ]]; then
    [[ -d "$dir" ]] || die "Managed path exists but is not a directory: $dir"
    [[ -f "$dir/.arm-owned" && "$(cat "$dir/.arm-owned")" == "$MARKER" ]] || die "Refusing to claim an existing unmarked directory: $dir"
  else
    mkdir -p "$dir"
    printf '%s\n' "$MARKER" > "$dir/.arm-owned"
  fi
  chmod 700 "$dir"
}

ensure_owned_dir "$CONFIG_DIR"
ensure_owned_dir "$STATE_DIR"
ensure_owned_dir "$LOG_DIR"
[[ ! -L "$BIN_DIR" ]] || die "Refusing symlinked binary directory: $BIN_DIR"
[[ ! -L "$UNIT_DIR" ]] || die "Refusing symlinked systemd unit directory: $UNIT_DIR"
mkdir -p "$BIN_DIR" "$UNIT_DIR"

assert_manager_file() {
  local path="$1"
  if [[ -L "$path" ]]; then
    die "Refusing symlinked manager output path: $path"
  elif [[ -e "$path" ]]; then
    [[ -f "$path" ]] || die "Refusing non-file manager output path: $path"
    grep -Fq "$MARKER" "$path" || die "Refusing to overwrite a file not owned by this manager: $path"
  fi
}
assert_manager_file "$BIN_DIR/autoremove-manager"
assert_manager_file "$UNIT_DIR/auto-remove-manager.service"
assert_manager_file "$UNIT_DIR/auto-remove-manager.timer"

for config_file in manager.json config.yml owner; do
  [[ ! -L "$CONFIG_DIR/$config_file" ]] || die "Refusing symlinked configuration file: $CONFIG_DIR/$config_file"
done
[[ ! -e "$CONFIG_DIR/manager.json" || -f "$CONFIG_DIR/manager.json" ]] || die "manager.json is not a regular file."
[[ ! -e "$CONFIG_DIR/config.yml" || -f "$CONFIG_DIR/config.yml" ]] || die "config.yml is not a regular file."

VENV="$BASE/venv"
[[ ! -L "$VENV" ]] || die "Refusing symlinked virtual environment path: $VENV"
if [[ -e "$VENV" && ! -d "$VENV" ]]; then
  die "Virtual environment path exists but is not a directory: $VENV"
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  if ! python3 -m venv "$VENV"; then
    if [[ "$MODE" == root && -f /etc/debian_version ]] && has apt-get; then
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y --no-install-recommends python3-venv
      python3 -m venv "$VENV" || die "Unable to create the Python virtual environment."
    else
      die "Unable to create a virtual environment. Install python3-venv, then rerun."
    fi
  fi
fi

export PIP_NO_CACHE_DIR=1
if "$VENV/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3,9))'; then
  "$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade pip
else
  # pip 25+ dropped Python 3.8; keep the last compatible pip line on legacy hosts.
  "$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "pip<25"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$BASE/requirements.txt"

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
    "schedule": {"enabled": True},
    "racing": {
        "enabled": False,
        "preview_only": True,
        "allow_removal": False,
        "allow_data_deletion": False,
        "download_threshold_mb": 100.0,
        "upload_max_mb": 10.0,
        "ratio_min": 1.0,
        "sustain_seconds": 5.0,
        "poll_interval_seconds": 1.0
    },
    "hybrid": {
        "enabled": False,
        "preview_only": True,
        "allow_removal": False,
        "allow_data_deletion": False,
        "download_threshold_mb": 100.0,
        "upload_max_mb": 10.0,
        "ratio_min": 1.0,
        "sustain_seconds": 5.0,
        "poll_interval_seconds": 1.0,
        "cleanup_interval_seconds": 60.0
    }
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
printf '%s\n' "$RUN_USER" > "$CONFIG_DIR/owner"
chmod 600 "$CONFIG_DIR/manager.json" "$CONFIG_DIR/config.yml" "$CONFIG_DIR/owner"

cat > "$BIN_DIR/autoremove-manager" <<EOF
#!/usr/bin/env bash
# $MARKER
set -Eeuo pipefail
export PATH="$VENV/bin:$BIN_DIR:/usr/local/bin:/usr/bin:/bin"
exec "$VENV/bin/python" "$BASE/src/manager.py" \\
  --mode "$MODE" \\
  --config "$CONFIG_DIR/manager.json" \\
  --upstream-config "$CONFIG_DIR/config.yml" \\
  --state-dir "$STATE_DIR" \\
  --log-dir "$LOG_DIR" "\$@"
EOF
chmod 755 "$BIN_DIR/autoremove-manager"

if [[ "$MODE" == root ]]; then
  chown -R "$RUN_USER":"$(id -gn "$RUN_USER")" "$BASE" "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR"
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

  if has systemctl && [[ -d /run/systemd/system ]]; then
    systemctl daemon-reload
    systemctl enable --now auto-remove-manager.timer
    ok "System timer enabled (preview mode)."
  elif has crontab; then
    install_marked_cron "$RUN_USER" "$BIN_DIR/autoremove-manager"
    warn "systemd is unavailable; installed a marked preview cron job for $RUN_USER."
  else
    warn "Neither systemd nor crontab is available; scheduling is disabled."
  fi
else
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
    install_marked_cron "" "$BIN_DIR/autoremove-manager"
    warn "User systemd is unavailable; installed a marked per-user hourly preview cron job."
  else
    warn "No user systemd or crontab found; scheduler is not enabled."
  fi
fi

ok "$APP_NAME installed in $MODE mode."
info "Scanning locally installed torrent clients..."
"$BIN_DIR/autoremove-manager" detect || warn "Client discovery returned an error; inspect the installation and try again."
if [[ -t 0 ]]; then
  read -r -p "Configure detected supported clients now? [Y/n] " configure_now
  if [[ ! "$configure_now" =~ ^[Nn]$ ]]; then
    if [[ "$MODE" == root && "$RUN_USER" != "root" ]]; then
      if has runuser; then
        runuser -u "$RUN_USER" -- env HOME="$RUN_HOME" "$BIN_DIR/autoremove-manager" configure || warn "One or more client configurations were not saved. Retry as $RUN_USER."
      else
        warn "Could not switch to $RUN_USER safely. Run '$BIN_DIR/autoremove-manager configure' as $RUN_USER (without sudo)."
      fi
    else
      "$BIN_DIR/autoremove-manager" configure || warn "One or more client configurations were not saved. You can retry with the configure command."
    fi
  fi
fi
info "Review proposed removals: $BIN_DIR/autoremove-manager preview"
warn "Actual torrent removal is disabled. Downloaded-data deletion is also disabled."
warn "Uninstall with: $BIN_DIR/autoremove-manager uninstall"
