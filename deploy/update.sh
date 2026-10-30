#!/usr/bin/env bash
#
# Pull the latest FlowPilot and restart it.
#
#   bash /opt/flowpilot/deploy/update.sh
#
# Your database, password and run history live in flowpilot.db and are untouched.

set -euo pipefail

APP_USER="${APP_USER:-flowpilot}"
APP_DIR="${APP_DIR:-/opt/flowpilot}"

say()  { printf '\n\033[1;35m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mError:\033[0m %s\n\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this as root:  sudo bash update.sh"
[ -d "$APP_DIR/.git" ] || die "No FlowPilot install at $APP_DIR. Run install.sh first."

# Git refuses to work on a repository owned by another user, so every git call
# runs as the account that owns the checkout.
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
as_app() { sudo -u "$APP_USER" git -C "$APP_DIR" "$@"; }

say "Current version"
info "$(as_app log --oneline -1)"

say "Fetching latest"
as_app fetch --quiet origin
if [ -z "$(as_app log HEAD..origin/main --oneline)" ]; then
    info "already up to date"
else
    as_app log HEAD..origin/main --oneline | sed 's/^/    /'
    as_app reset --hard --quiet origin/main
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

say "Updating dependencies"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/playwright" install chromium >/dev/null
info "done"

say "Restarting"
systemctl restart flowpilot
sleep 2
systemctl is-active --quiet flowpilot || die "Service failed to start. Check: journalctl -u flowpilot -n 50"
info "running: $(as_app log --oneline -1)"
echo
