#!/usr/bin/env bash
# Auto-Remove-Manager scoped one-click uninstaller
# SPDX-License-Identifier: MIT
set -Eeuo pipefail
IFS=$'\n\t'

MODE=""
YES=0
MARKER="AUTO-REMOVE-MANAGER-OWNED-V1"
RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok(){ printf '%s✓ %s%s\n' "$GREEN" "$*" "$RESET"; }
warn(){ printf '%s! %s%s\n' "$YELLOW" "$*" "$RESET"; }
die(){ printf '%s✗ %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }

remove_marked_cron() {
  local target_user="$1"
  local cron_tmp cron_clean
  command -v crontab >/dev/null 2>&1 || return 0
  cron_tmp="$(mktemp)"
  cron_clean="$(mktemp)"
  if [[ -n "$target_user" ]]; then
    crontab -u "$target_user" -l > "$cron_tmp" 2>/dev/null || true
  else
    crontab -l > "$cron_tmp" 2>/dev/null || true
  fi
  if grep -q '# AUTO-REMOVE-MANAGER$' "$cron_tmp"; then
    grep -v '# AUTO-REMOVE-MANAGER$' "$cron_tmp" > "$cron_clean" || true
    if [[ -n "$target_user" ]]; then
      crontab -u "$target_user" "$cron_clean"
    else
      crontab "$cron_clean"
    fi
    ok "Removed this manager's marked cron entry."
  fi
  rm -f "$cron_tmp" "$cron_clean"
}

while (($#)); do
  case "$1" in
    --mode) (($# >= 2)) || die "--mode requires root or rootless"; MODE="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --help|-h) echo "Usage: bash uninstall.sh --mode root|rootless [--yes]"; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ "$MODE" == root || "$MODE" == rootless ]] || die "Specify --mode root or rootless."

if [[ "$MODE" == root ]]; then
  [[ "$EUID" -eq 0 ]] || die "Root uninstall requires root."
  BASE="/opt/auto-remove-manager"
  CONFIG_DIR="/etc/auto-remove-manager"
  STATE_DIR="/var/lib/auto-remove-manager"
  BIN="/usr/local/bin/autoremove-manager"
  UNIT_DIR="/etc/systemd/system"
  CRON_USER=""
  if [[ -f "$CONFIG_DIR/owner" ]]; then
    CRON_USER="$(cat "$CONFIG_DIR/owner")"
  fi
else
  [[ "$EUID" -ne 0 ]] || die "Rootless uninstall must run as the installing user. Do not use sudo."
  BASE="$HOME/.local/share/auto-remove-manager"
  CONFIG_DIR="$HOME/.config/auto-remove-manager"
  STATE_DIR="$HOME/.local/state/auto-remove-manager"
  BIN="$HOME/.local/bin/autoremove-manager"
  UNIT_DIR="$HOME/.config/systemd/user"
  CRON_USER=""
fi

cat <<EOF
This will remove Auto-Remove-Manager-owned items for mode: $MODE
  Installation: $BASE
  Configuration and credentials: $CONFIG_DIR
  State and logs: $STATE_DIR
  Command wrapper: $BIN
  Service/timer: auto-remove-manager.service / auto-remove-manager.timer

It will NOT remove torrent clients, client configurations, downloads, or torrent payload data.
EOF

if ((YES == 0)); then
  read -r -p "Type REMOVE to permanently uninstall Auto-Remove-Manager: " answer
  [[ "$answer" == "REMOVE" ]] || { echo "Cancelled."; exit 0; }
fi

service_path="$UNIT_DIR/auto-remove-manager.service"
timer_path="$UNIT_DIR/auto-remove-manager.timer"
service_owned=0
timer_owned=0
if [[ -f "$service_path" ]] && grep -Fq "$MARKER" "$service_path"; then service_owned=1; fi
if [[ -f "$timer_path" ]] && grep -Fq "$MARKER" "$timer_path"; then timer_owned=1; fi

if [[ "$MODE" == root ]]; then
  if ((timer_owned)); then systemctl disable --now auto-remove-manager.timer >/dev/null 2>&1 || true; fi
  if ((service_owned)); then systemctl stop auto-remove-manager.service >/dev/null 2>&1 || true; fi
else
  if ((timer_owned)); then systemctl --user disable --now auto-remove-manager.timer >/dev/null 2>&1 || true; fi
  if ((service_owned)); then systemctl --user stop auto-remove-manager.service >/dev/null 2>&1 || true; fi
fi

for unit in auto-remove-manager.service auto-remove-manager.timer; do
  path="$UNIT_DIR/$unit"
  if [[ -f "$path" ]] && grep -Fq "$MARKER" "$path"; then
    rm -f -- "$path"
    ok "Removed owned unit: $unit"
  elif [[ -e "$path" ]]; then
    warn "Left non-manager unit untouched: $path"
  fi
done

if [[ "$MODE" == root ]]; then
  systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ -n "$CRON_USER" ]]; then
    remove_marked_cron "$CRON_USER"
  fi
else
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  remove_marked_cron ""
fi

if [[ -f "$BIN" ]] && grep -Fq "$MARKER" "$BIN"; then
  rm -f -- "$BIN"
  ok "Removed command wrapper."
elif [[ -e "$BIN" ]]; then
  warn "Left non-manager command untouched: $BIN"
fi

remove_owned_dir() {
  local path="$1"
  if [[ -L "$path" ]]; then
    warn "Refusing to follow symlink; left untouched: $path"
  elif [[ -d "$path" && -f "$path/.arm-owned" ]] && [[ "$(cat "$path/.arm-owned")" == "$MARKER" ]]; then
    rm -rf -- "$path"
    ok "Removed owned directory: $path"
  elif [[ -e "$path" ]]; then
    warn "Ownership marker missing or mismatched; left untouched: $path"
  fi
}
remove_owned_dir "$CONFIG_DIR"
remove_owned_dir "$STATE_DIR"
remove_owned_dir "$BASE"

ok "Auto-Remove-Manager ($MODE) uninstall completed."
echo "Torrent clients, their configuration, downloads, and torrent data were not touched."
