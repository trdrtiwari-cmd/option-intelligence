#!/usr/bin/env bash
# Option Intelligence - one-command setup on an Oracle Cloud "Always Free" Ubuntu 24.04 server (v1.20.93).
#
#   Run on the server as the normal "ubuntu" user (NOT with sudo):
#       bash ~/option-intelligence-repo/option-intelligence/oracle-setup.sh
#
#   It asks for: your DuckDNS name + token, the GitLab token (only if the code is not downloaded yet), and - once - the old
#   database address (Neon) to copy your data from. Safe to run again: it updates instead of starting over.
#
#   What it does
#     1. installs Python, git, PostgreSQL and Caddy (Caddy gives the site free, automatic HTTPS); Indian time zone;
#        automatic security updates
#     2. opens ports 80 and 443 in the server's own firewall (Oracle's Ubuntu blocks them by default)
#     3. keeps your DuckDNS name pointed at this server
#     4. downloads your code from GitLab and installs it
#     5. creates the database on THIS server and copies your data from the old one (account, settings, Upstox token,
#        signal log, paper trades, AI reviews) - the old database is only read
#     6. creates ~/oi.env (your settings - same names as the Render "Environment" page)
#     7. runs the app as a service that starts on boot and restarts if it crashes
#     8. updates itself from GitLab every 15 minutes - only while the market is closed - and backs up the database every night
set -euo pipefail
trap 'printf "\n\033[1;31mERROR: the step at line %s failed (exit %s) - send this line to Claude.\033[0m\n" "$LINENO" "$?"' ERR

GITLAB_REPO="gitlab.com/trdrtiwari1/option-intelligence.git"
REPO_DIR="$HOME/option-intelligence-repo"
ENV_FILE="$HOME/oi.env"
SETTINGS="$HOME/.oi-setup"          # remembers the DuckDNS name / token between runs (only you can read it)
PORT=8000

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] && die "Run this as the ubuntu user (not with sudo):  bash $0"
command -v apt-get >/dev/null || die "This script is for Ubuntu. Create the server with the Canonical Ubuntu 24.04 image."

# ------------------------------------------------------------------ questions
[ -f "$SETTINGS" ] && . "$SETTINGS"
if [ -z "${DUCK_NAME:-}" ]; then
  read -rp "DuckDNS name (only the first part, e.g. myoptions for myoptions.duckdns.org): " DUCK_NAME
fi
DUCK_NAME="${DUCK_NAME%%.duckdns.org}"
[ -n "$DUCK_NAME" ] || die "DuckDNS name is required."
if [ -z "${DUCK_TOKEN:-}" ]; then
  read -rsp "DuckDNS token (shown at the top of duckdns.org after you sign in; it is not shown while you paste): " DUCK_TOKEN; echo
fi
[ -n "$DUCK_TOKEN" ] || die "DuckDNS token is required."
DOMAIN="$DUCK_NAME.duckdns.org"
umask 077
printf 'DUCK_NAME=%q\nDUCK_TOKEN=%q\n' "$DUCK_NAME" "$DUCK_TOKEN" > "$SETTINGS"
umask 022

# ------------------------------------------------------------------ 1. packages, time zone, security updates
say "Installing Python, git, PostgreSQL and Caddy (3-6 minutes)"
sudo timedatectl set-timezone Asia/Kolkata || true
sudo apt-get -o DPkg::Lock::Timeout=900 update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=900 install -y python3 python3-venv python3-pip git curl postgresql \
     debian-keyring debian-archive-keyring apt-transport-https gnupg iptables-persistent unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get -o DPkg::Lock::Timeout=900 update -y
  sudo apt-get -o DPkg::Lock::Timeout=900 install -y caddy || sudo apt-get -o DPkg::Lock::Timeout=900 install -y caddy   # retry once (slow mirrors)
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
grep -q OK "$HOME/duckdns/duck.log" 2>/dev/null && ok "DuckDNS OK" || warn "DuckDNS did not answer OK - check the name and token (you can run this script again)."

# ------------------------------------------------------------------ 4. code from GitLab
say "Getting the code from GitLab"
git config --global credential.helper store
if [ ! -d "$REPO_DIR/.git" ]; then
  read -rsp "GitLab access token (scope read_repository; it is not shown while you paste): " GL_TOKEN; echo
  [ -n "$GL_TOKEN" ] || die "GitLab token is required for a private project."
  umask 077; printf 'https://oauth2:%s@gitlab.com\n' "$GL_TOKEN" > "$HOME/.git-credentials"; umask 022
  git clone "https://$GITLAB_REPO" "$REPO_DIR" || die "Download failed - check the token and that it has read_repository."
else
  # cloned with the token in the address (the guide's first command): keep the token in ~/.git-credentials instead
  url=$(git -C "$REPO_DIR" remote get-url origin)
  if printf '%s' "$url" | grep -q 'oauth2:'; then
    tok=$(printf '%s' "$url" | sed -E 's#https://oauth2:([^@]+)@.*#\1#')
    umask 077; printf 'https://oauth2:%s@gitlab.com\n' "$tok" > "$HOME/.git-credentials"; umask 022
    git -C "$REPO_DIR" remote set-url origin "https://$GITLAB_REPO"
  fi
  git -C "$REPO_DIR" pull --ff-only
fi

# The project holds an old copy at the top and the current one in a sub-folder: use the newest APP_VERSION.
find_app() {
python3 - "$REPO_DIR" <<'PY'
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
}
APP_DIR=$(find_app)
[ -n "$APP_DIR" ] || die "Could not find the app (main.py with APP_VERSION) in the GitLab project."
VERSION=$(grep -m1 -oP '^APP_VERSION\s*=\s*"\K[\d.]+' "$APP_DIR/main.py")
echo "App folder: $APP_DIR  (version $VERSION)"

say "Installing the Python packages (3-8 minutes the first time)"
python3 -m venv "$HOME/oi-venv"
"$HOME/oi-venv/bin/pip" install -q --upgrade pip wheel
"$HOME/oi-venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# ------------------------------------------------------------------ 5. database on this server
say "Creating the database on this server"
DB_PW_FILE="$HOME/.oi-dbpw"
if [ ! -f "$DB_PW_FILE" ]; then
  umask 077; head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > "$DB_PW_FILE"; umask 022
fi
DB_PW=$(cat "$DB_PW_FILE")
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='oi'" | grep -q 1 \
  || sudo -u postgres psql -qc "CREATE ROLE oi LOGIN PASSWORD '$DB_PW'"
sudo -u postgres psql -qc "ALTER ROLE oi PASSWORD '$DB_PW'"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='oi'" | grep -q 1 \
  || sudo -u postgres createdb -O oi oi
LOCAL_DB="postgresql://oi:$DB_PW@127.0.0.1:5432/oi"
ok "Database ready (it only listens inside this server)"

# ------------------------------------------------------------------ 6. settings file
NEW_ENV=0
if [ ! -f "$ENV_FILE" ]; then
  NEW_ENV=1
  umask 077
  {
    echo "# Option Intelligence settings - copy every variable from Render > Environment here, ONE KEY=value per line,"
    echo "# no spaces around =. Do NOT copy DATABASE_URL, RENDER..., KEEPALIVE... - they are set below for this server."
    echo "UPSTOX_API_KEY="
    echo "UPSTOX_API_SECRET="
    echo "UPSTOX_REDIRECT_URI=https://$DOMAIN/auth/callback"
    echo "GEMINI_API_KEY="
    echo "VAPID_PUBLIC_KEY="
    echo "VAPID_PRIVATE_KEY="
    echo "VAPID_SUBJECT=mailto:you@example.com"
    echo "# Must be ONE line if used (the whole JSON on a single line)"
    echo "FIREBASE_SERVICE_ACCOUNT_JSON="
    echo "# ---- other variables from Render below this line ----"
    echo ""
    echo "# ---- set by the setup script (do not change) ----"
    echo "KEEPALIVE=0"
  } > "$ENV_FILE"
  umask 022
fi
# this server's database (replaces any older DATABASE_URL line)
grep -v '^DATABASE_URL=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
printf 'DATABASE_URL=%s\n' "$LOCAL_DB" >> "$ENV_FILE.tmp"
grep -q '^KEEPALIVE=' "$ENV_FILE.tmp" || echo "KEEPALIVE=0" >> "$ENV_FILE.tmp"
mv "$ENV_FILE.tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"

# ------------------------------------------------------------------ copy the data from the old database (once)
if [ ! -f "$HOME/.oi-data-copied" ]; then
  say "Copying your data from the old database (Neon)"
  echo "Paste the old database address - the DATABASE_URL value from Render > Environment (it starts with postgresql://)."
  read -rsp "Old database address (not shown while you paste; press Enter to skip): " OLD_DB; echo
  if [ -n "$OLD_DB" ]; then
    ( cd "$APP_DIR" && DATABASE_URL="$OLD_DB" OI_TARGET="$LOCAL_DB" "$HOME/oi-venv/bin/python" - <<'PY'
import os, sys
sys.path.insert(0, os.getcwd())
import accounts as acc
r = acc.copy_to(os.environ["OI_TARGET"], overwrite=True)
for t in r["tables"]:
    print(f"  {t['table']:<22} {t['source']:>6} -> {t['copied']:<6} {'OK' if t['ok'] else 'MISMATCH'}")
sys.exit(0 if r["ok"] else 1)
PY
    ) && { touch "$HOME/.oi-data-copied"; ok "All data copied and checked. The old database was not changed."; } \
      || warn "The copy did not finish - run this script again to retry (nothing is lost)."
  else
    warn "Skipped - the app starts with an empty database. Run this script again to copy later."
  fi
fi

# ------------------------------------------------------------------ 7. service + HTTPS
say "Starting the app as a service"
sudo tee /etc/systemd/system/oi.service >/dev/null <<EOF
[Unit]
Description=Option Intelligence
After=network-online.target postgresql.service
Wants=network-online.target postgresql.service

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

# ------------------------------------------------------------------ 8. update, automatic update, backup
cat > "$HOME/oi-update.sh" <<EOF
#!/usr/bin/env bash
# Get the newest version from GitLab and restart the app (run by hand, or by the automatic update below).
set -e
git -C "$REPO_DIR" pull --ff-only -q
APP_DIR=\$(python3 - "$REPO_DIR" <<'PY'
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
"$HOME/oi-venv/bin/pip" install -q -r "\$APP_DIR/requirements.txt"
if ! grep -q "WorkingDirectory=\$APP_DIR\$" /etc/systemd/system/oi.service; then
  sudo sed -i "s#^WorkingDirectory=.*#WorkingDirectory=\$APP_DIR#" /etc/systemd/system/oi.service
  sudo systemctl daemon-reload
fi
sudo systemctl restart oi
sleep 6
if systemctl is-active --quiet oi && curl -fsS -m 10 http://127.0.0.1:$PORT/health >/dev/null; then
  echo "\$(date '+%F %T') updated and running: \$(grep -m1 -oP '^APP_VERSION\s*=\s*"\K[\d.]+' "\$APP_DIR/main.py")"
else
  echo "\$(date '+%F %T') the app did not start - last log lines:"; sudo journalctl -u oi -n 30 --no-pager
fi
EOF
chmod +x "$HOME/oi-update.sh"

cat > "$HOME/oi-autoupdate.sh" <<EOF
#!/usr/bin/env bash
# Every 15 min: if GitLab has a newer version, update - but never during market hours (Mon-Fri 09:00-15:45 IST).
dow=\$(date +%u); hm=\$(date +%H%M)
if [ "\$dow" -le 5 ] && [ "\$hm" -ge 0900 ] && [ "\$hm" -le 1545 ]; then exit 0; fi
git -C "$REPO_DIR" fetch -q origin || exit 0
[ "\$(git -C "$REPO_DIR" rev-parse HEAD)" = "\$(git -C "$REPO_DIR" rev-parse @{u})" ] && exit 0
"$HOME/oi-update.sh" >> "$HOME/oi-update.log" 2>&1
EOF
chmod +x "$HOME/oi-autoupdate.sh"

mkdir -p "$HOME/backups"
cat > "$HOME/oi-backup.sh" <<EOF
#!/usr/bin/env bash
# Nightly copy of the database (kept 14 days). Restore one with:  pg_restore -c -d "\$DATABASE_URL" FILE
PGPASSWORD="$DB_PW" pg_dump -h 127.0.0.1 -U oi -Fc oi > "$HOME/backups/oi-\$(date +%F).dump" && \
find "$HOME/backups" -name 'oi-*.dump' -mtime +14 -delete
EOF
chmod 700 "$HOME/oi-backup.sh"

( { crontab -l 2>/dev/null || true; } | { grep -v -e duckdns/duck.sh -e oi-autoupdate.sh -e oi-backup.sh || true; }
  echo "*/5 * * * * $HOME/duckdns/duck.sh >/dev/null 2>&1"
  echo "*/15 * * * * $HOME/oi-autoupdate.sh >/dev/null 2>&1"
  echo "30 2 * * * $HOME/oi-backup.sh >/dev/null 2>&1" ) | crontab -

# ------------------------------------------------------------------ check
sleep 6
if curl -fsS -m 10 "http://127.0.0.1:$PORT/health" >/dev/null; then
  say "Done. The app is running (version $VERSION)."
else
  warn "The app has not answered yet. See the log with:  sudo journalctl -u oi -n 50 --no-pager"
fi
echo
echo "  Your address:   https://$DOMAIN   (the HTTPS certificate can take 1-2 minutes the first time)"
echo "  Upstox redirect URL (Upstox developer app, only if you use 'Connect Upstox'):  https://$DOMAIN/auth/callback"
echo "  New versions: upload to GitLab as usual - this server picks them up within 15 min after the market closes."
echo "  Update now by hand:   ~/oi-update.sh        App log:   sudo journalctl -u oi -f"
echo "  Database backups:     ~/backups (every night at 02:30, kept 14 days)"
if [ "$NEW_ENV" = 1 ]; then
  echo
  warn "NEXT: put your settings in (copy the values from Render > Environment):"
  echo "      nano $ENV_FILE"
  echo "  save with Ctrl+O, Enter, then Ctrl+X - and restart the app:"
  echo "      sudo systemctl restart oi"
fi
