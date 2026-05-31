import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from netaiops_asset.config_loader import get_config
from netaiops_asset.security.request_context import get_request_context


def _cleanup_old_audits(audit_dir: Path, retention_days: int) -> None:
    if retention_days <= 0:
        return

    cutoff = time.time() - retention_days * 86400
    for f in audit_dir.glob("audit_*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception:
            continue


def write_audit(event: dict[str, Any]) -> str:
    config = get_config()
    runtime = config.get("runtime", {})
    audit_dir = Path(runtime.get("audit_dir", "/var/lib/netaiops-asset-agent/data/audit"))
    retention_days = int(runtime.get("audit_retention_days", 90) or 90)

    audit_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_audits(audit_dir, retention_days)

    request_id = event.get("request_id") or str(uuid.uuid4())
    ctx = get_request_context()

    payload = {
        "request_id": request_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "client_ip": ctx.get("client_ip"),
        "user_agent": ctx.get("user_agent"),
        "http_method": ctx.get("method"),
        "http_path": ctx.get("path"),
        "auth_user": event.get("auth_user") or event.get("user") or "unknown",
        **event,
    }
    payload["request_id"] = request_id

    audit_file = audit_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with audit_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return request_id
