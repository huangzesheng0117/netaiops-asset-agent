#!/usr/bin/env bash
set -u

cd /opt/netaiops-asset-agent || exit 1

if [ -f /etc/netaiops-asset-agent/asset-agent.env ]; then
  set -a
  . /etc/netaiops-asset-agent/asset-agent.env
  set +a
fi

if [ -x /opt/netaiops-asset-agent/venv/bin/python ]; then
  PY=/opt/netaiops-asset-agent/venv/bin/python
else
  PY=python3
fi

echo "========== V2 status check =========="
echo "PWD=$(pwd)"
echo "PY=$PY"
$PY -V

echo
echo "========== service health =========="
curl -sS --max-time 5 http://127.0.0.1:18081/health | $PY -m json.tool || true

echo
echo "========== V2 py_compile =========="
$PY -m py_compile \
  netaiops_asset/mcp/client.py \
  netaiops_asset/mcp/netmiko_client.py \
  netaiops_asset/mcp/prometheus_client.py \
  netaiops_asset/device_identity/resolver.py \
  netaiops_asset/observability/promql_guard.py \
  netaiops_asset/observability/prometheus_query.py \
  netaiops_asset/netmiko/cli_guard.py \
  netaiops_asset/netmiko/executor.py \
  netaiops_asset/troubleshoot/session.py \
  netaiops_asset/troubleshoot/evidence_builder.py \
  tools/regress_v2_acceptance.py

echo
echo "========== V2 acceptance =========="
$PY tools/regress_v2_acceptance.py
