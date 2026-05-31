#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/netaiops-asset-agent"
PY="$APP_DIR/venv/bin/python"

cd "$APP_DIR"
source venv/bin/activate

echo "========== V1 Acceptance Check =========="
echo

echo "===== health ====="
curl -s http://127.0.0.1:18081/health | jq '{status,service,version,llm_enabled,llm_model,cmdb_mode,cmdb_env,cmdb_base_url}'
echo

echo "===== run regression tests ====="
for script in \
  tools/regress_v1_cmdb.py \
  tools/regress_v1_llm.py \
  tools/regress_v1_conversation_actions.py \
  tools/regress_v1_llm_first.py \
  tools/regress_v1_field_lookup_export.py \
  tools/regress_v1_local_filter.py
do
  if [ -f "$script" ]; then
    echo
    echo "----- $script -----"
    "$PY" "$script" --base-url http://127.0.0.1:18081
  else
    echo "skip missing: $script"
  fi
done

echo
echo "========== V1 Acceptance Check Completed =========="
