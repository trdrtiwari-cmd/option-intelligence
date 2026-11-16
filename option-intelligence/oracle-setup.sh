#!/usr/bin/env bash
# Option Intelligence - one-command setup on an Oracle Cloud "Always Free" Ubuntu server.
#
#   Run on the server (as the normal "ubuntu" user, NOT with sudo):
#       bash oracle-setup.sh
#
#   It asks for: your DuckDNS name + token, and a GitLab access token (read_repository).
#   Safe to run again: it updates the code and settings instead of starting over.
#
#   What it does
#     1. installs Python, git and Caddy (Caddy gives the site free, automatic HTTPS)
#     2. opens ports 80 and 443 in the server's own firewall (Oracle's Ubuntu blocks them by default)
#     3. keeps your DuckDNS name pointed at this server
#     4. downloads your code from GitLab and installs it
#     5. creates ~/oi.env (your settings - same names as the Render "Environment" page)
#     6. runs the app as a service that starts on boot and restarts if it crashes
#     7. creates ~/oi-update.sh  (run it after every new version is pushed to GitLab)
set -euo pipefail

GITLAB_REPO="gitlab.com/trdrtiwari/option-intelligence.git"
REPO_DIR="$HOME/option-intelligence-repo"
ENV_FILE="$HOME/oi.env"
SETTINGS="$HOME/.oi-setup"          # remembers the DuckDNS name between runs
PORT=8000

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] && die "Run this as the ubuntu user:  bash oracle-setup.sh   (not with sudo)"
command -v apt-get >/dev/null || die "This script is for Ubuntu. Create the server with the Canonical Ubuntu image."

# ------------------------------------------------------------------ questions
[ -f "$SETTINGS" ] && . "$SETTINGS"
if [ -z "${DUCK_NAME:-}" ]; then
  read -rp "DuckDNS name (only the first part, e.g. myoptions for myoptions.duckdns.org): " DUCK_NAME
fi
DUCK_NAME="${DUCK_NAME%%.duckdns.org}"
[ -n "$DUCK_NAME" ] || die "DuckDNS name is required."
if [ -z "${DUCK_TOKEN:-}" ]; then
  read -rsp "DuckDNS token (shown at the top of duckdns.org after you sign in): " DUCK_TOKEN; echo
fi
[ -n "$DUCK_TOKEN" ] || die "DuckDNS token is required."
DOMAIN="$DUCK_NAME.duckdns.org"
umask 077
printf 'DUCK_NAME=%q\nDUCK_TOKEN=%q\n' "$DUCK_NAME" "$DUCK_TOKEN" > "$SETTINGS"
umask 022

# ------------------------------------------------------------------ 1. packages
say "Installing Python, git and Caddy (2-5 minutes)"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip git curl \
     debian-keyring debian-archive-keyring apt-transport-https gnupg iptables-persistent
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y caddy || sudo apt-get install -y caddy   # retry once (slow mirrors)
fi

# ------------------------------------------------------------------ 2. firewall inside the server
say "Opening ports 80 and 443 in the server firewall"
for p in 80 443; do
  if ! sudo iptables -C INPUT -p tcp -m state --state NEW --dport "$p" -j ACCEPT 2>/dev/null; then
    line=$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
    if [ -n "$line" ]; then sudo iptables -I INPUT "$line" -p tcp -m state --state NEW --dport "$p" -j ACCEPT
    else sudo iptables -A INPUT -p tcp -m state --state NEW --dport "$p" -j ACCEPT; fi
  fi
done
sudo netfilter-persistent save >/dev/null

# ------------------------------------------------------------------ 3. DuckDNS
say "Pointing $DOMAIN at this server"
mkdir -p "$HOME/duckdns"
cat > "$HOME/duckdns/duck.sh" <<EOF
#!/bin/sh
curl -fsS -m 20 "https://www.duckdns.org/update?domains=$DUCK_NAME&token=$DUCK_TOKEN&ip=" -o "$HOME/duckdns/duck.log"
EOF
chmod 700 "$HOME/duckdns/duck.sh"
"$HOME/duckdns/duck.sh" || true
grep -q OK "$HOME/duckdns/duck.log" 2>/dev/null || warn "DuckDNS did not answer OK - check the name and token (you can run this script again)."
( crontab -l 2>/dev/null | grep -v duckdns/duck.sh; echo "*/5 * * * * $HOME/duckdns/duck.sh >/dev/null 2>&1" ) | crontab -

# ------------------------------------------------------------------ 4. code from GitLab
say "Downloading the code from GitLab"
if [ ! -d "$REPO_DIR/.git" ]; then
  read -rsp "GitLab access token (Preferences > Access tokens, scope read_repository): " GL_TOKEN; echo
  [ -n "$GL_TOKEN" ] || die "GitLab token is required for a private project."
  git config --global credential.helper store
  umask 077; printf 'https://oauth2:%s@gitlab.com\n' "$GL_TOKEN" > "$HOME/.git-credentials"; umask 022
  git clone "https://$GITLAB_REPO" "$REPO_DIR" || die "Clone failed - check the token and that it has read_repository."
else
  git -C "$REPO_DIR" pull --ff-only
fi

# The project holds an old copy at the top and the current one in a sub-folder: use the newest APP_VERSION.
APP_DIR=$(python3 - "$REPO_DIR" <<'PY'
import os, re, sys
best = None
for root, dirs, files in os.walk(sys.argv[1]):
    dirs[:] = [d for d in dirs if d not in (".git", "venv", "__pycache__", "node_modules")]
    if "main.py" in files and "requirements.txt" in files:
        txt = open(os.path.join(root, "main.py"), encoding="utf-8", errors="ignore").read()
        m = re.search(r'^APP_VERSION\s*=\s*"([\d.]+)"', txt, re.M)
        if m:
            v = tuple(int(x) for x in m.group(1).split("."))
            if best is None or v > best[0]:
                best = (v, root)
print(best[1] if best else "")
PY
)
[ -n "$APP_DIR" ] || die "Could not find the app (main.py with APP_VERSION) in the GitLab project."
VERSION=$(grep -m1 -oP '^APP_VERSION\s*=\s*"\K[\d.]+' "$APP_DIR/main.py")
echo "App folder: $APP_DIR  (version $VERSION)"

say "Installing the Python packages (3-8 minutes the first time)"
python3 -m venv "$HOME/oi-venv"
"$HOME/oi-venv/bin/pip" install --upgrade pip wheel >/dev/null
"$HOME/oi-venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ------------------------------------------------------------------ 5. settings file
if [ ! -f "$ENV_FILE" ]; then
  say "Creating your settings file $ENV_FILE"
  umask 077
  {
    echo "# Option Intelligence settings - same names as the Render Environment page. One KEY=value per line, no spaces around ="
    echo "UPSTOX_API_KEY="
    echo "UPSTOX_API_SECRET="
    echo "UPSTOX_REDIRECT_URI=https://$DOMAIN/auth/callback"
    echo "SIGNUP_CODE="
    echo "# Leave empty to keep accounts + signal log in a file on this server (it is kept across restarts here),"
    echo "# or paste your existing PostgreSQL URL to keep using that database."
    echo "DATABASE_URL="
    echo "TRADINGVIEW_WEBHOOK_SECRET="
    echo "TELEGRAM_BOT_TOKEN="
    echo "TELEGRAM_CHAT_ID="
    echo "VAPID_PUBLIC_KEY="
    echo "VAPID_PRIVATE_KEY="
    echo "VAPID_SUBJECT=mailto:you@example.com"
    echo "# Must be ONE line if used (the whole JSON on a single line)"
    echo "FIREBASE_SERVICE_ACCOUNT_JSON="
    echo "# Copy any other variables you set on Render below this line."
    echo "# The Render keep-awake ping is not needed here:"
    echo "KEEPALIVE=0"
  } > "$ENV_FILE"
  umask 022
  NEW_ENV=1
else
  NEW_ENV=0
  grep -q '^KEEPALIVE=' "$ENV_FILE" || echo "KEEPALIVE=0" >> "$ENV_FILE"
fi

# ------------------------------------------------------------------ 6. service + HTTPS
say "Starting the app as a service"
sudo tee /etc/systemd/system/oi.service >/dev/null <<EOF
[Unit]
Description=Option Intelligence
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$HOME/oi-venv/bin/uvicorn main:app --host 127.0.0.1 --port $PORT --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable oi >/dev/null 2>&1
sudo systemctl restart oi

sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DOMAIN {
    reverse_proxy 127.0.0.1:$PORT
}
EOF
sudo systemctl enable caddy >/dev/null 2>&1
sudo systemctl restart caddy

# ------------------------------------------------------------------ 7. update script
cat > "$HOME/oi-update.sh" <<EOF
#!/usr/bin/env bash
# Pull the newest version from GitLab and restart the app.
set -e
git -C "$REPO_DIR" pull --ff-only
"$HOME/oi-venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
sudo systemctl restart oi
sleep 4
systemctl is-active --quiet oi && echo "Updated and running: \$(grep -m1 -oP '^APP_VERSION\s*=\s*"\K[\d.]+' "$APP_DIR/main.py")" || { echo "App did not start - last log lines:"; sudo journalctl -u oi -n 30 --no-pager; }
EOF
chmod +x "$HOME/oi-update.sh"

# ------------------------------------------------------------------ check
sleep 5
if curl -fsS -m 10 "http://127.0.0.1:$PORT/health" >/dev/null; then
  say "Done. The app is running."
else
  warn "The app has not answered yet. See the log with:  sudo journalctl -u oi -n 50 --no-pager"
fi
echo
echo "  Your address:   https://$DOMAIN   (the HTTPS certificate can take 1-2 minutes the first time)"
echo "  Upstox redirect URL to set in the Upstox developer app:  https://$DOMAIN/auth/callback"
echo
if [ "$NEW_ENV" = 1 ]; then
  warn "NEXT: fill in your settings (copy the values from Render > Environment):"
  echo "      nano $ENV_FILE"
  echo "  then restart the app:"
  echo "      sudo systemctl restart oi"
fi
echo "  After a new version is pushed to GitLab:   ~/oi-update.sh"
echo "  App log:                                   sudo journalctl -u oi -f"
