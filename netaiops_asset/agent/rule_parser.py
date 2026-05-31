import re
from typing import Any

from netaiops_asset.cmdb.adapter import extract_ip
from netaiops_asset.cmdb.field_map import normalize_fields


def _extract_idc(text: str) -> str | None:
    m = re.search(r"(万国SH\d+|浦江SH\d+|SH\d+|万国88|WG88|周浦|龙田路)", text or "", re.IGNORECASE)
    if not m:
        return None

    value = m.group(1)
    if value.upper() == "WG88":
        return "万国88"
    if re.fullmatch(r"SH\d+", value, re.IGNORECASE):
        return value.upper()
    return value


def _extract_rack(text: str) -> str | None:
    patterns = [
        r"([A-Z]\d{2})\s*(?:机柜|机架|rack)",
        r"(?:机柜|机架|rack)\s*([A-Z]\d{2})",
    ]

    for p in patterns:
        m = re.search(p, text or "", re.IGNORECASE)
        if m:
            return m.group(1).upper()

    return None


def _extract_room(text: str, rack: str | None = None) -> str | None:
    """
    提取 CMDB server_room 字段。

    重要修复：
    - “SH16机房”在用户口语里通常表示 IDC=SH16，不是 server_room=H16。
    - 旧规则会在 “SH16机房” 中间误匹配出 “H16机房”。
    - 新规则通过 (?<![A-Za-z]) 防止从 SH16 这类 IDC 字符串中截取 H16。
    - 数字机房如 203/404 仍然正常识别。
    """

    raw = text or ""

    patterns = [
        r"(?:机房|server_room)\s*(?:为|是|:|：)?\s*([0-9]{3,4})",
        r"(?<![A-Za-z])([0-9]{3,4})\s*(?:机房|server_room)",
        r"(?:机房|server_room)\s*(?:为|是|:|：)?\s*((?<![A-Za-z])[A-Z]\d{2})",
        r"(?<![A-Za-z])([A-Z]\d{2})\s*(?:机房|server_room)",
    ]

    for p in patterns:
        m = re.search(p, raw, re.IGNORECASE)
        if not m:
            continue

        value = m.group(1).upper()

        if re.fullmatch(r"SH\d+", value, re.IGNORECASE):
            return None

        if rack and value == rack:
            return None

        return value

    return None


def _extract_vendor(text: str) -> str | None:
    mapping = {
        "cisco": "CISCO",
        "思科": "CISCO",
        "fortinet": "Fortinet",
        "飞塔": "Fortinet",
        "f5": "F5",
        "h3c": "H3C",
        "华三": "H3C",
        "huawei": "Huawei",
        "华为": "Huawei",
    }

    low = (text or "").lower()
    for k, v in mapping.items():
        if k.lower() in low:
            return v

    return None


def _extract_device_type(text: str) -> str | None:
    if "交换机" in text:
        return "交换机"
    if "防火墙" in text:
        return "防火墙"
    if "负载" in text or "ltm" in text.lower():
        return "负载均衡"
    return None


def _extract_status(text: str) -> str | None:
    if "在线" in text:
        return "在线"
    if "下电" in text:
        return "下电"
    if "在用" in text:
        return "在用"
    if "离线" in text:
        return "离线"
    return None


def _extract_fields(text: str) -> list[str]:
    fields = []
    mapping = [
        ("主机名", "host_name"),
        ("hostname", "host_name"),
        ("管理ip", "mgmt_ip"),
        ("管理地址", "mgmt_ip"),
        ("序列号", "sn"),
        ("sn", "sn"),
        ("型号", "device_spec"),
        ("厂商", "manufacturer"),
        ("品牌", "band"),
        ("状态", "status"),
        ("用途", "comment"),
        ("机房", "server_room"),
        ("机柜", "rack"),
        ("机架", "rack"),
        ("idc", "IDC"),
        ("em码", "server_ID"),
        ("资产编码", "server_ID"),
        ("操作系统", "os_version"),
        ("端口映射", "port_server_maps"),
        ("接口总数", "total_interface_num"),
        ("在线接口数", "online_interface_num"),
        ("prometheus", "prometheus_monitor"),
        ("elk", "log_to_elk"),
        ("3a", "auth_3a"),
        ("维保开始", "maintain_startdate"),
        ("维保结束", "maintain_enddate"),
        ("维保到期", "maintain_enddate"),
        ("维保", "maintain_enddate"),
        ("合同", "oa_contract"),
        ("费控", "costcontrol_ticket_id"),
    ]

    low = (text or "").lower()
    for key, field in mapping:
        if key.lower() in low and field not in fields:
            fields.append(field)

    return normalize_fields(fields)


def parse_question(question: str) -> dict[str, Any]:
    text = question or ""
    ip = extract_ip(text)
    fields = _extract_fields(text)

    if ip:
        return {
            "intent": "query_device_detail",
            "keyword": ip,
            "fields": fields,
            "reason": "detected_ip",
        }

    filters: dict[str, Any] = {}

    rack = _extract_rack(text)
    idc = _extract_idc(text)
    room = _extract_room(text, rack=rack)
    vendor = _extract_vendor(text)
    device_type = _extract_device_type(text)
    status = _extract_status(text)

    if idc:
        filters["IDC__icontains"] = idc
    if room:
        filters["server_room__icontains"] = room
    if rack:
        filters["rack__icontains"] = rack
    if vendor:
        filters["manufacturer__icontains"] = vendor
    if device_type:
        filters["ci_type__icontains"] = device_type
    if status:
        filters["status__icontains"] = status

    model_match = re.search(
        r"(N9K-[A-Z0-9\-]+|C\d{4,}[A-Z0-9\-]*|FortiGate-[A-Z0-9\-]+)",
        text,
        re.IGNORECASE,
    )
    if model_match:
        filters["device_spec__icontains"] = model_match.group(1)

    if filters:
        return {
            "intent": "query_devices",
            "filters": filters,
            "fields": fields,
            "reason": "detected_filters",
        }

    if len(text.strip()) >= 2:
        return {
            "intent": "query_devices",
            "filters": {"search": text.strip()},
            "fields": fields,
            "reason": "fallback_search",
        }

    return {
        "intent": "clarify",
        "message": "请补充查询条件，例如 IDC、机房、机柜、主机名、管理IP、厂商或设备型号。",
        "fields": fields,
    }
