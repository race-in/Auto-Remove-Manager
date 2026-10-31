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

while (($#)); do
  case "$1" in
    --mode) (($# >= 2)) || die "--mode requires root or rootless"; MODE="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --help|-h) echo "Usage: bash uninstall.sh --mode root|rootless [--yes]"; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ "$MODE" == root || "$MODE" == rootless ]] || die "Specify --mode root or --mode rootless."

if [[ "$MODE" == root ]]; then
  [[ "$EUID" -eq 0 ]] || die "Root uninstall requires root."
  BASE="/opt/auto-remove-manager"
  CONFIG_DIR="/etc/auto-remove-manager"
  STATE_DIR="/var/lib/auto-remove-manager"
  BIN="/usr/local/bin/autoremove-manager"
  UNIT_DIR="/etc/systemd/system"
else
  [[ "$EUID" -ne 0 ]] || die "Rootless uninstall must run as the installing user. Do not use sudo."
  BASE="$HOME/.local/share/auto-remove-manager"
  CONFIG_DIR="$HOME/.config/auto-remove-manager"
  STATE_DIR="$HOME/.local/state/auto-remove-manager"
  BIN="$HOME/.local/bin/autoremove-manager"
  UNIT_DIR="$HOME/.config/systemd/user"
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

if [[ "$MODE" == root ]]; then
  systemctl disable --now auto-remove-manager.timer >/dev/null 2>&1 || true
  systemctl stop auto-remove-manager.service >/dev/null 2>&1 || true
else
  systemctl --user disable --now auto-remove-manager.timer >/dev/null 2>&1 || true
  systemctl --user stop auto-remove-manager.service >/dev/null 2>&1 || true
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
else
  systemctl --user daemon-reload >/dev/null 2>&1 || true
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

if [[ "$MODE" == rootless ]] && command -v crontab >/dev/null 2>&1; then
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  if crontab -l > "$tmp" 2>/dev/null; then
    if grep -q '# AUTO-REMOVE-MANAGER$' "$tmp"; then
      sed '/# AUTO-REMOVE-MANAGER$/d' "$tmp" | crontab -
      ok "Removed this manager's marked cron entry."
    fi
  fi
fi

ok "Auto-Remove-Manager ($MODE) uninstall completed."
echo "Torrent clients, their configuration, downloads, and torrent data were not touched."
