#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/netaiops-asset-agent"

cd "$APP_DIR"
source venv/bin/activate

echo "===== systemd ====="
systemctl status netaiops-asset-agent --no-pager || true

echo
echo "===== listen port ====="
ss -lntp | egrep ':18081|State' || true

echo
echo "===== health ====="
curl -s http://127.0.0.1:18081/health | jq . || true

echo
echo "===== selfcheck ====="
curl -s http://127.0.0.1:18081/api/v1/selfcheck | jq . || true

echo
echo "===== llm config masked ====="
curl -s http://127.0.0.1:18081/api/v1/llm/config | jq '{status,llm:{enabled:.llm.enabled,base_url:.llm.base_url,model:.llm.model,api_key_env:.llm.api_key_env,api_key_configured:.llm.api_key_configured,response_format:.llm.response_format,thinking:.llm.thinking}}' || true

echo
echo "===== recent journal ====="
journalctl -u netaiops-asset-agent -n 80 --no-pager || true

echo
echo "===== disk ====="
df -h / /opt /var 2>/dev/null || df -h
