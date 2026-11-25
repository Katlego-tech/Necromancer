#!/usr/bin/env bash
# Read the whole SQLite index once so it sits in the page cache before the first visitor.
# Measured on a dev machine (see .kiro/specs/necromancer.md T7.9): the first find_person query takes
# about 9 s on a cold cache and 0.2 s on a warm one; reading the file sequentially takes about 2 s.
# On EBS it can be slower, so run this before the service accepts traffic (systemd ExecStartPre).
# Usage: warm_index.sh [path-to-enron.sqlite]   (default /opt/necromancer/data/enron/enron.sqlite)
set -euo pipefail
DB="${1:-/opt/necromancer/data/enron/enron.sqlite}"
if [ ! -r "$DB" ]; then
  echo "warm_index: $DB is not readable, skipping" >&2
  exit 0
fi
start=$(date +%s)
cat "$DB" > /dev/null
echo "warm_index: read $(stat -c %s "$DB") bytes in $(( $(date +%s) - start )) s"
