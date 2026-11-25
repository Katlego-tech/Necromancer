#!/bin/bash
# EC2 user-data: fetch the repo and run deploy/bootstrap.sh on first boot (runs as root).
# Pass with:  aws ec2 run-instances ... --user-data file://deploy/user-data.sh
# Progress and errors: /var/log/cloud-init-output.log. Bootstrap starts no service.
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/Katlego-tech/Necromancer.git}"
REPO_REF="${REPO_REF:-main}"
dnf install -y git
[ -d /opt/necromancer/.git ] || git clone "$REPO_URL" /opt/necromancer
REPO_URL="$REPO_URL" REPO_REF="$REPO_REF" bash /opt/necromancer/deploy/bootstrap.sh
