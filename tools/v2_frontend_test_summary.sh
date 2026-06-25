#!/usr/bin/env bash
set -u

cd /opt/netaiops-asset-agent || exit 1

echo "========== V2 frontend test summary =========="
echo "Time: $(date '+%F %T')"
echo "Host: $(hostname)"
echo

echo "========== Service =========="
systemctl status netaiops-asset-agent --no-pager -l | sed -n '1,25p'

echo
echo "========== Health =========="
curl -sS --max-time 8 http://127.0.0.1:18081/health | python3 -m json.tool || true

echo
echo "========== URL =========="
echo "Web: http://10.191.97.151:18081/"
echo

echo "========== Manual frontend test cases =========="
sed -n '1,220p' docs/v2_frontend_test_cases.md

echo
echo "========== Recent reports =========="
ls -lh /tmp/v2_web_smoke_regress_*.json 2>/dev/null | tail -n 5 || true
ls -lh /tmp/v2_prometheus_cpu_chat_regress_*.json 2>/dev/null | tail -n 3 || true
ls -lh /tmp/v2_execution_analysis_regress_*.json 2>/dev/null | tail -n 3 || true
