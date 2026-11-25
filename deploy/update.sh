#!/usr/bin/env bash
# Deploy a commit, tag or branch, and remember the previous one so a rollback is one command.
#   sudo /opt/necromancer/deploy/update.sh <git-ref>        # deploy
#   sudo /opt/necromancer/deploy/update.sh --rollback       # go back to the previous ref
# Changes only code and the venv. The index, var/ (caps and usage log) and the env files are kept.
set -euo pipefail
APP_DIR=/opt/necromancer
PREV_FILE=/etc/necromancer/previous-ref
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ $# -eq 1 ] || { echo "usage: $0 <git-ref> | --rollback" >&2; exit 2; }

current=$(git -C "$APP_DIR" rev-parse HEAD)
if [ "$1" = "--rollback" ]; then
  [ -f "$PREV_FILE" ] || { echo "no previous ref recorded" >&2; exit 1; }
  target=$(cat "$PREV_FILE")
else
  git -C "$APP_DIR" fetch --tags origin
  target="$1"
fi
git -C "$APP_DIR" checkout --detach "$target" 2>/dev/null || git -C "$APP_DIR" checkout --detach "origin/$target"
echo "$current" > "$PREV_FILE"
"$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"
install -m 644 "$APP_DIR/deploy/necromancer.service" /etc/systemd/system/necromancer.service
install -m 644 "$APP_DIR/deploy/caddy.service" /etc/systemd/system/caddy.service
systemctl daemon-reload
systemctl restart necromancer.service
systemctl reload-or-restart caddy.service
echo "now at $(git -C "$APP_DIR" rev-parse --short HEAD); previous was $(echo "$current" | cut -c1-7)"
echo "check: systemctl status necromancer --no-pager; then deploy/smoke_test.py"
