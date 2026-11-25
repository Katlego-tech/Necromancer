#!/usr/bin/env bash
# Generate a new access code, store it in /etc/necromancer/necromancer.env (root only, mode 600),
# print it once, and restart the app so the old code stops working.
#   sudo /opt/necromancer/deploy/set_access_code.sh            # random code
#   sudo /opt/necromancer/deploy/set_access_code.sh --no-restart
# The code is never put on a command line, in the repo, or in the logs. Copy it from this output.
set -euo pipefail
ENV_FILE=/etc/necromancer/necromancer.env
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing; run bootstrap.sh first" >&2; exit 1; }

code=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | cut -c1-16)
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
grep -v '^NECRO_ACCESS_CODE=' "$ENV_FILE" > "$tmp" || true
printf 'NECRO_ACCESS_CODE=%s\n' "$code" >> "$tmp"     # printf is a shell builtin: no argv exposure
install -m 600 -o root -g root "$tmp" "$ENV_FILE"

echo "New access code (shown once): $code"
if [ "${1:-}" != "--no-restart" ] && systemctl is-enabled necromancer.service >/dev/null 2>&1; then
  systemctl restart necromancer.service
  echo "necromancer.service restarted"
fi
