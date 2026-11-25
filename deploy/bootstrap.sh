#!/usr/bin/env bash
# Prepare a fresh Amazon Linux 2023 instance (x86_64 or arm64) for the Necromancer voice server.
# Run once as root, from the instance (SSH) or as EC2 user-data. Safe to run again.
#   sudo REPO_URL=https://github.com/Katlego-tech/Necromancer.git REPO_REF=main bash bootstrap.sh
#
# It installs OS packages, Python 3.12 and the pinned requirements, a pinned Caddy, a non-root
# service user and the systemd units. It does NOT start the services: they need the Enron index,
# an access code and a hostname first (deploy/RUNBOOK.md steps 5 to 8). It makes no AWS API call.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Katlego-tech/Necromancer.git}"
REPO_REF="${REPO_REF:-main}"
APP_DIR=/opt/necromancer
CONF_DIR=/etc/necromancer
CADDY_VERSION=2.11.9
# CADDY_SHA256 below (per CPU) is the SHA-256 of the official release tarball, GitHub release v$CADDY_VERSION, read 25 Nov 2026.

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }

case "$(uname -m)" in
  x86_64)  CADDY_ARCH=amd64; CADDY_SHA256=5b8cb7faf8726a2028ad96a66e54e6e81e8f361bc6175e5d22bbd997cc5177fb ;;
  aarch64) CADDY_ARCH=arm64; CADDY_SHA256=5e377cff994fc9362a6fcf695c53f226cfca3a3ef85bd3a3687cf252d6310165 ;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

echo "== 1/6 OS packages"
dnf install -y python3.12 python3.12-pip git tar gzip

echo "== 2/6 service users and directories"
id necro >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin necro
id caddy >/dev/null 2>&1 || useradd --system --home-dir /var/lib/caddy --shell /sbin/nologin caddy
install -d -m 755 "$APP_DIR"
install -d -m 700 "$CONF_DIR"
install -d -o caddy -g caddy -m 700 /var/lib/caddy

echo "== 3/6 application code ($REPO_URL @ $REPO_REF)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --tags origin
else
  git clone "$REPO_URL" "$APP_DIR"
fi
git -C "$APP_DIR" checkout --detach "$REPO_REF" 2>/dev/null || git -C "$APP_DIR" checkout --detach "origin/$REPO_REF"
# Code is owned by root (the service cannot modify it); only var/ and data/ are writable by the service.
install -d -o necro -g necro -m 750 "$APP_DIR/var" "$APP_DIR/data" "$APP_DIR/data/enron" "$APP_DIR/data/wayback"
# Emergency switch directory (NECRO_CONTROL_DIR): `touch disable-judge` or `disable-public` here cuts a code off.
install -d -o necro -g necro -m 750 "$APP_DIR/var/control"

echo "== 4/6 Python 3.12 venv and pinned requirements"
[ -x "$APP_DIR/.venv/bin/python" ] || python3.12 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" - <<'PY'
import sqlite3, sys
print("python", sys.version.split()[0], "sqlite", sqlite3.sqlite_version)
db = sqlite3.connect(":memory:")
try:
    db.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
except sqlite3.OperationalError:
    sys.exit("this Python's SQLite has no FTS5; the Enron search needs it")
import aiohttp, awscrt  # noqa: F401  (native wheels must import on this CPU)
import aws_sdk_bedrock_runtime  # noqa: F401
print("FTS5 and the Bedrock SDK import fine")
PY

echo "== 5/6 Caddy $CADDY_VERSION ($CADDY_ARCH, checksum pinned)"
if ! /usr/local/bin/caddy version 2>/dev/null | grep -q "v$CADDY_VERSION"; then
  tmp=$(mktemp -d)
  tarball="caddy_${CADDY_VERSION}_linux_${CADDY_ARCH}.tar.gz"
  curl -fsSL -o "$tmp/$tarball" "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/${tarball}"
  echo "${CADDY_SHA256}  $tmp/$tarball" | sha256sum -c -
  tar -xzf "$tmp/$tarball" -C "$tmp" caddy
  install -m 755 "$tmp/caddy" /usr/local/bin/caddy
  rm -rf "$tmp"
fi

echo "== 6/6 systemd units and config templates"
install -m 644 "$APP_DIR/deploy/necromancer.service" /etc/systemd/system/necromancer.service
install -m 644 "$APP_DIR/deploy/caddy.service" /etc/systemd/system/caddy.service
[ -e "$CONF_DIR/necromancer.env" ] || install -m 600 "$APP_DIR/deploy/necromancer.env.example" "$CONF_DIR/necromancer.env"
[ -e "$CONF_DIR/caddy.env" ] || install -m 600 "$APP_DIR/deploy/caddy.env.example" "$CONF_DIR/caddy.env"
chmod 755 "$APP_DIR"/deploy/*.sh
systemctl daemon-reload
systemctl enable necromancer.service caddy.service

echo
echo "Bootstrap done. Nothing is started yet. Next (deploy/RUNBOOK.md):"
echo "  1. copy the index to $APP_DIR/data/enron/enron.sqlite (owner necro)"
echo "  2. sudo $APP_DIR/deploy/set_access_code.sh"
echo "  3. edit $CONF_DIR/caddy.env (NECRO_SITE), then: systemctl start necromancer caddy"
