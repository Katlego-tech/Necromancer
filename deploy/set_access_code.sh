#!/usr/bin/env bash
# Generate a new access code, store it in /etc/necromancer/necromancer.env (root only, mode 600),
# print it once, and restart the app so the old code stops working.
#   sudo /opt/necromancer/deploy/set_access_code.sh            # random public code
#   sudo /opt/necromancer/deploy/set_access_code.sh --judge    # random judge code (for the Devpost form)
#   sudo /opt/necromancer/deploy/set_access_code.sh --no-restart   (combine with --judge as needed)
# The code is never put on a command line, in the repo, or in the logs. Copy it from this output.
set -euo pipefail
ENV_FILE=/etc/necromancer/necromancer.env
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE is missing; run bootstrap.sh first" >&2; exit 1; }

var=NECRO_ACCESS_CODE
label="access code"
restart=1
for arg in "$@"; do
  case "$arg" in
    --judge) var=NECRO_JUDGE_CODE; label="JUDGE code" ;;
    --no-restart) restart=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
# 20 characters: above the server's 12-character minimum for the judge code. The two codes are
# random and independent, so they differ; the server also refuses to start if they are equal.
code=$(head -c 30 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
grep -v "^${var}=" "$ENV_FILE" > "$tmp" || true
printf '%s=%s\n' "$var" "$code" >> "$tmp"     # printf is a shell builtin: no argv exposure
install -m 600 -o root -g root "$tmp" "$ENV_FILE"

echo "New $label (shown once): $code"
if [ "$restart" -eq 1 ] && systemctl is-enabled necromancer.service >/dev/null 2>&1; then
  systemctl restart necromancer.service
  echo "necromancer.service restarted"
fi
