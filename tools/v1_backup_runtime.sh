#!/usr/bin/env bash
set -Eeuo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
OUT="/opt/netaiops-asset-agent/backup/runtime_v1_${TS}"
APP_DIR="/opt/netaiops-asset-agent"

mkdir -p "$OUT"

cp -a /etc/netaiops-asset-agent "$OUT/etc_netaiops_asset_agent" 2>/dev/null || true
cp -a "$APP_DIR/app.py" "$OUT/app.py" 2>/dev/null || true
cp -a "$APP_DIR/netaiops_asset" "$OUT/netaiops_asset" 2>/dev/null || true
cp -a "$APP_DIR/tools" "$OUT/tools" 2>/dev/null || true
cp -a "$APP_DIR/docs" "$OUT/docs" 2>/dev/null || true

find "$OUT" -type f -name 'asset-agent.env' -exec chmod 600 {} \; 2>/dev/null || true
find "$OUT" -type f -name '*.bak' -exec chmod 600 {} \; 2>/dev/null || true

echo "runtime_backup_dir=$OUT"
du -sh "$OUT" || true
