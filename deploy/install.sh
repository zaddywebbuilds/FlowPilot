#!/usr/bin/env bash
#
# FlowPilot installer for a fresh Debian or Ubuntu server.
#
#   With a domain (recommended, gets a real certificate automatically):
#     DOMAIN=flowpilot.example.com bash install.sh
#
#   Without a domain (private, reached over an SSH tunnel):
#     bash install.sh
#
# Re-running this is safe: it updates an existing install in place.

set -euo pipefail

DOMAIN="${DOMAIN:-}"
REPO="${REPO:-https://github.com/zaddywebbuilds/FlowPilot.git}"
APP_USER="${APP_USER:-flowpilot}"
APP_DIR="${APP_DIR:-/opt/flowpilot}"
PORT="${PORT:-8000}"

say()  { printf '\n\033[1;35m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mError:\033[0m %s\n\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this as root:  sudo bash install.sh"
command -v apt-get >/dev/null || die "This script expects Debian or Ubuntu."

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates \
    debian-keyring debian-archive-keyring apt-transport-https >/dev/null
info "done"

say "Creating the $APP_USER service account"
if id "$APP_USER" >/dev/null 2>&1; then
    info "already exists"
else
    useradd --system --create-home --home-dir "/home/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
    info "created"
fi

say "Fetching FlowPilot into $APP_DIR"
# Git refuses to work on a repository owned by another user, so every git call
# runs as the account that owns the checkout.
if [ -d "$APP_DIR/.git" ]; then
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch --quiet origin
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard --quiet origin/main
    info "updated to $(sudo -u "$APP_USER" git -C "$APP_DIR" log --oneline -1)"
else
    mkdir -p "$APP_DIR"
    chown "$APP_USER:$APP_USER" "$APP_DIR"
    sudo -u "$APP_USER" git clone --quiet "$REPO" "$APP_DIR"
    info "cloned $(sudo -u "$APP_USER" git -C "$APP_DIR" log --oneline -1)"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "Installing Python dependencies"
if [ ! -d "$APP_DIR/.venv" ]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
info "done"

say "Installing Chromium and its system libraries"
# --with-deps needs root for apt, the browser itself belongs to the service account.
"$APP_DIR/.venv/bin/playwright" install-deps chromium >/dev/null
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/playwright" install chromium >/dev/null
info "done"

say "Writing the systemd service"
cat > /etc/systemd/system/flowpilot.service <<SERVICE
[Unit]
Description=FlowPilot automation control center
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=FLOWPILOT_HEADLESS=1
Environment=FLOWPILOT_BROWSER_CHANNEL=
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/uvicorn app:app --app-dir backend --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=5

# The service only ever needs its own directory.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$APP_DIR

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --quiet --now flowpilot
systemctl restart flowpilot
sleep 2
systemctl is-active --quiet flowpilot || die "Service failed to start. Check: journalctl -u flowpilot -n 50"
info "running and enabled at boot"

if [ -n "$DOMAIN" ]; then
    say "Setting up HTTPS for $DOMAIN"
    if ! command -v caddy >/dev/null; then
        curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
            | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
            > /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -qq
        apt-get install -y -qq caddy >/dev/null
    fi

    CADDYFILE=/etc/caddy/Caddyfile
    SNIPPET_DIR=/etc/caddy/conf.d
    BACKUP="$CADDYFILE.before-flowpilot.$(date +%Y%m%d-%H%M%S)"

    # This server may already be serving other sites. Never overwrite the main
    # Caddyfile: keep FlowPilot in its own snippet and import it.
    mkdir -p "$SNIPPET_DIR"
    if [ -f "$CADDYFILE" ]; then
        cp -a "$CADDYFILE" "$BACKUP"
        info "backed up existing Caddyfile to $BACKUP"
        EXISTING=$(grep -cE '^[[:space:]]*[a-z0-9.*:-]+[[:space:]]*\{' "$CADDYFILE" || true)
        if [ "${EXISTING:-0}" -gt 0 ]; then
            info "it already defines $EXISTING site block(s), leaving them alone"
        fi
    else
        touch "$CADDYFILE"
    fi

    cat > "$SNIPPET_DIR/flowpilot.caddy" <<CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:$PORT
}
CADDY

    if ! grep -qE "import[[:space:]]+$SNIPPET_DIR/\*" "$CADDYFILE"; then
        printf '\nimport %s/*.caddy\n' "$SNIPPET_DIR" >> "$CADDYFILE"
        info "added an import line for $SNIPPET_DIR"
    fi

    if ! caddy validate --adapter caddyfile --config "$CADDYFILE" >/dev/null 2>&1; then
        if [ -f "$BACKUP" ]; then
            cp -a "$BACKUP" "$CADDYFILE"
            rm -f "$SNIPPET_DIR/flowpilot.caddy"
            die "Caddy rejected the new config. Your original Caddyfile has been restored, nothing changed."
        fi
        die "Caddy rejected the generated config at $CADDYFILE"
    fi

    systemctl reload caddy 2>/dev/null || systemctl restart caddy
    info "Caddy will request a certificate automatically"
fi

say "Done"
echo
if [ -n "$DOMAIN" ]; then
    info "Open:  https://$DOMAIN"
    echo
    info "If that does not load, $DOMAIN probably is not pointing at this server yet."
    info "Add a DNS A record for it, then run:  systemctl restart caddy"
else
    info "FlowPilot is listening on 127.0.0.1:$PORT and is NOT reachable from"
    info "the internet, which is deliberate. Reach it from your own machine with:"
    echo
    info "    ssh -L $PORT:127.0.0.1:$PORT root@\$(curl -s ifconfig.me)"
    echo
    info "then open  http://127.0.0.1:$PORT  in your browser."
    echo
    info "To send someone a link instead, you need a domain name, because no"
    info "certificate authority will issue HTTPS for a bare IP address."
    info "A free one takes two minutes at https://www.duckdns.org: create a"
    info "subdomain, point it at this server's IP, then re-run:"
    echo
    info "    DOMAIN=yourname.duckdns.org bash install.sh"
fi
echo
info "The first page you see asks you to create a password. Until you do,"
info "nothing else is reachable."
echo
info "Logs:     journalctl -u flowpilot -f"
info "Restart:  systemctl restart flowpilot"
info "Update:   bash $APP_DIR/deploy/update.sh"
echo
