from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from netaiops_asset.config_loader import get_config


_MIN_FIELDS = [
    {"name": "host_name", "cn_name": "主机名", "description": "设备主机名"},
    {"name": "mgmt_ip", "cn_name": "管理IP", "description": "设备管理IP"},
    {"name": "sn", "cn_name": "设备序列号", "description": "设备序列号"},
    {"name": "device_spec", "cn_name": "设备型号", "description": "设备型号"},
    {"name": "status", "cn_name": "状态", "description": "设备状态"},
    {"name": "IDC", "cn_name": "IDC", "description": "IDC"},
    {"name": "server_room", "cn_name": "机房", "description": "机房"},
    {"name": "rack", "cn_name": "机架", "description": "机架/机柜"},
]
_MIN_ALIASES = {
    "主机名": "host_name",
    "hostname": "host_name",
    "管理ip": "mgmt_ip",
    "管理地址": "mgmt_ip",
    "ip": "mgmt_ip",
    "序列号": "sn",
    "设备序列号": "sn",
    "型号": "device_spec",
    "设备型号": "device_spec",
    "状态": "status",
    "idc": "IDC",
    "IDC": "IDC",
    "机房": "server_room",
    "机柜": "rack",
    "机架": "rack",
}
_MIN_DEFAULT = ["host_name", "mgmt_ip", "sn", "device_spec", "status", "IDC", "server_room", "rack"]
_MIN_DETAIL = _MIN_DEFAULT.copy()
_MIN_FILTERS = set(_MIN_DEFAULT)
_MIN_SENSITIVE = set()


def _load_field_map() -> dict[str, Any]:
    cfg = get_config()
    path = Path(cfg.get("cmdb", {}).get("field_map_path", "/etc/netaiops-asset-agent/field_map.yaml"))
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _fields() -> list[dict[str, Any]]:
    data = _load_field_map()
    value = data.get("fields")
    if isinstance(value, list) and value:
        return value
    return _MIN_FIELDS


def _aliases() -> dict[str, str]:
    data = _load_field_map()
    value = data.get("aliases")
    if isinstance(value, dict) and value:
        return {str(k): str(v) for k, v in value.items()}
    return _MIN_ALIASES


def _list(name: str, fallback: list[str]) -> list[str]:
    data = _load_field_map()
    value = data.get(name)
    if isinstance(value, list) and value:
        return [str(x) for x in value]
    return fallback.copy()


CMDB_FIELDS = _fields()
FIELD_ALIASES = _aliases()
DEFAULT_FIELDS = _list("default_fields", _MIN_DEFAULT)
DETAIL_FIELDS = _list("detail_fields", _MIN_DETAIL)
QUERY_FILTER_FIELDS = set(_list("query_filter_fields", sorted(_MIN_FILTERS)))
SENSITIVE_FIELDS = set(_list("sensitive_fields", sorted(_MIN_SENSITIVE)))


def reload_field_map() -> None:
    global CMDB_FIELDS, FIELD_ALIASES, DEFAULT_FIELDS, DETAIL_FIELDS, QUERY_FILTER_FIELDS, SENSITIVE_FIELDS
    CMDB_FIELDS = _fields()
    FIELD_ALIASES = _aliases()
    DEFAULT_FIELDS = _list("default_fields", _MIN_DEFAULT)
    DETAIL_FIELDS = _list("detail_fields", _MIN_DETAIL)
    QUERY_FILTER_FIELDS = set(_list("query_filter_fields", sorted(_MIN_FILTERS)))
    SENSITIVE_FIELDS = set(_list("sensitive_fields", sorted(_MIN_SENSITIVE)))


def normalize_field_name(name: str) -> str:
    raw = str(name or "").strip()
    key = raw.lower()
    return FIELD_ALIASES.get(raw, FIELD_ALIASES.get(key, raw))


def normalize_fields(fields: list[str] | str | None, default: list[str] | None = None) -> list[str]:
    if not fields:
        return (default or DEFAULT_FIELDS).copy()

    if isinstance(fields, str):
        raw = [x.strip() for x in fields.replace("，", ",").split(",") if x.strip()]
    else:
        raw = [str(x).strip() for x in fields if str(x).strip()]

    known = {x["name"] for x in CMDB_FIELDS}
    result = []
    for item in raw:
        normalized = normalize_field_name(item)
        if normalized in known and normalized not in result:
            result.append(normalized)

    return result or (default or DEFAULT_FIELDS).copy()


def field_labels() -> dict[str, str]:
    return {x["name"]: x.get("cn_name", x["name"]) for x in CMDB_FIELDS}
