import io
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from netaiops_asset.agent.conversation_actions import detect_conversation_action, handle_conversation_action
from netaiops_asset.agent.conversation_store import (
    append_turn,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
)
from netaiops_asset.agent.rule_parser import parse_question
from netaiops_asset.cmdb.adapter import CMDBAdapter
from netaiops_asset.cmdb.field_map import CMDB_FIELDS, field_labels, normalize_fields
from netaiops_asset.config_loader import CONFIG_PATH, get_config
from netaiops_asset.llm.client import LLMClient
from netaiops_asset.llm.tool_planner import apply_llm_plan, plan_with_llm
from netaiops_asset.llm.planner_policy import accept_llm_parse, build_planner_diagnostics, should_try_llm
from netaiops_asset.security.audit import write_audit
from netaiops_asset.security.request_context import reset_request_context, set_request_context
from netaiops_asset.tools.cmdb_tools import CMDB_TOOL_CATALOG, tool_query_cmdb_device_detail, tool_query_cmdb_devices, tool_query_cmdb_devices_by_ips
from netaiops_asset.web.ui import render_index_html


CONFIG = get_config()
APP_NAME = CONFIG.get("app", {}).get("name", "netaiops-asset-agent")
APP_VERSION = CONFIG.get("app", {}).get("version", "0.1.0-v1-batch17-conversation-history")
START_TIME = time.time()

app = FastAPI(title=APP_NAME, version=APP_VERSION)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else ""

    token = set_request_context({
        "client_ip": client_ip,
        "user_agent": request.headers.get("user-agent", ""),
        "method": request.method,
        "path": request.url.path,
    })

    try:
        response = await call_next(request)
        return response
    finally:
        reset_request_context(token)


class ChatRequest(BaseModel):
    question: str
    user: str | None = "local_user"
    limit: int | None = 20
    conversation_id: str | None = None
    planner_mode: str | None = "llm"
    debug: bool | None = False


class ConversationCreateRequest(BaseModel):
    title: str | None = None
    user: str | None = "web_user"


class LLMParseRequest(BaseModel):
    question: str
    user: str | None = "web_user"
    force: bool | None = True

class LLMCompareRequest(BaseModel):
    question: str
    user: str | None = "web_user"
    planner_mode: str | None = "llm"

class ToolQueryDevicesRequest(BaseModel):
    filters: dict[str, Any] | None = None
    fields: list[str] | str | None = None
    page: int | None = 1
    page_size: int | None = 20
    user: str | None = "tool_user"


class ToolQueryDeviceDetailRequest(BaseModel):
    keyword: str
    fields: list[str] | str | None = None
    user: str | None = "tool_user"


class ToolQueryDevicesByIpsRequest(BaseModel):
    ips: list[str]
    fields: list[str] | str | None = None
    page_size: int | None = None
    user: str | None = "tool_user"


def build_answer(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        return f"CMDB 查询失败：{result.get('message', '未知错误')}"

    count = int(result.get("count") or 0)
    returned = int(result.get("returned") or 0)
    filters = result.get("filters", {})

    if count == 0:
        return "未查询到符合条件的 CMDB 网络设备记录。请确认 IDC、机房、机柜、主机名、管理IP 或型号是否正确。"

    limit_note = ""
    if count > returned:
        limit_note = f" 当前仅展示前 {returned} 条，可缩小查询条件；如需离线查看，可导出 Excel，单次最多导出 500 条。"

    if filters:
        filter_text = "，".join([f"{k}={v}" for k, v in filters.items()])
        return f"根据基金 CMDB 网络设备查询条件 {filter_text}，共查询到 {count} 条记录，本次返回 {returned} 条。{limit_note}"

    return f"基金 CMDB 网络设备查询完成，共查询到 {count} 条记录，本次返回 {returned} 条。{limit_note}"


@app.get("/", response_class=HTMLResponse)
def index_page() -> str:
    return render_index_html()


@app.get("/ui", response_class=HTMLResponse)
def ui_page() -> str:
    return render_index_html()


@app.get("/health")
def health() -> dict[str, Any]:
    config = get_config()
    cmdb = config.get("cmdb", {})
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
        "config_path": CONFIG_PATH,
        "uptime_seconds": int(time.time() - START_TIME),
        "v1_scope": "fund_cmdb_network_server_query",
        "cmdb_mode": cmdb.get("mode", "not_configured"),
        "cmdb_env": cmdb.get("env"),
        "cmdb_base_url": cmdb.get("api_base_url"),
        "features": config.get("features", {}),
        "llm_enabled": bool(config.get("llm", {}).get("enabled", False)),
        "llm_model": config.get("llm", {}).get("model"),
    }


@app.get("/api/v1/cmdb/schema")
def cmdb_schema() -> dict[str, Any]:
    return {
        "status": "ok",
        "data_source": "fund_cmdb_networkServer",
        "mode": get_config().get("cmdb", {}).get("mode", "not_configured"),
        "fields": CMDB_FIELDS,
        "field_labels": field_labels(),
    }


@app.get("/api/v1/cmdb/probe")
def cmdb_probe() -> dict[str, Any]:
    adapter = CMDBAdapter()
    result = adapter.probe()
    request_id = write_audit({
        "user": "api_user",
        "question": "api_cmdb_probe",
        "intent": "probe",
        "tool_name": "cmdb_probe",
        "tool_args": {},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    return result


@app.get("/api/v1/cmdb/devices")
def query_devices(
    search: str | None = Query(None),
    IDC: str | None = Query(None),
    server_room: str | None = Query(None),
    rack: str | None = Query(None),
    host_name: str | None = Query(None),
    mgmt_ip: str | None = Query(None),
    sn: str | None = Query(None),
    ci_type: str | None = Query(None),
    manufacturer: str | None = Query(None),
    band: str | None = Query(None),
    device_spec: str | None = Query(None),
    os_version: str | None = Query(None),
    env: str | None = Query(None),
    status: str | None = Query(None),
    tag: str | None = Query(None),
    maintenance_manufacturer: str | None = Query(None),
    fields: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    filters = {
        "search": search,
        "IDC__icontains": IDC,
        "server_room__icontains": server_room,
        "rack__icontains": rack,
        "host_name__icontains": host_name,
        "mgmt_ip": mgmt_ip,
        "sn__icontains": sn,
        "ci_type__icontains": ci_type,
        "manufacturer__icontains": manufacturer,
        "band__icontains": band,
        "device_spec__icontains": device_spec,
        "os_version__icontains": os_version,
        "env": env,
        "status__icontains": status,
        "tag__icontains": tag,
        "maintenance_manufacturer__icontains": maintenance_manufacturer,
    }
    filters = {k: v for k, v in filters.items() if v not in (None, "")}

    adapter = CMDBAdapter()
    result = adapter.query_devices(filters=filters, fields=fields, page=page, page_size=pageSize)
    request_id = write_audit({
        "user": "api_user",
        "question": "api_query_devices",
        "intent": "query_devices",
        "tool_name": "query_devices",
        "tool_args": {"filters": filters, "fields": fields, "page": page, "pageSize": pageSize},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    result["answer"] = build_answer(result)
    return result


@app.get("/api/v1/cmdb/device/detail")
def query_device_detail(
    keyword: str = Query(..., description="host_name, mgmt_ip, server_ID or sn"),
    fields: str | None = Query(None),
) -> dict[str, Any]:
    adapter = CMDBAdapter()
    result = adapter.query_device_detail(keyword=keyword, fields=fields)
    request_id = write_audit({
        "user": "api_user",
        "question": "api_query_device_detail",
        "intent": "query_device_detail",
        "tool_name": "query_device_detail",
        "tool_args": {"keyword": keyword, "fields": fields},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    result["answer"] = build_answer(result)
    return result


@app.get("/api/v1/cmdb/devices/by-ips")
def query_devices_by_ips(
    ips: str = Query(..., description="Comma, whitespace or newline separated management IPs"),
    fields: str | None = Query(None),
    pageSize: int = Query(100, ge=1, le=100),
) -> dict[str, Any]:
    raw_items = []
    for part in ips.replace("\n", ",").replace("\r", ",").replace(" ", ",").replace("，", ",").split(","):
        item = part.strip()
        if item:
            raw_items.append(item)

    ip_list = []
    for item in raw_items:
        if item not in ip_list:
            ip_list.append(item)

    if not ip_list:
        result = {"status": "error", "message": "ips is empty", "count": 0, "returned": 0, "items": []}
    else:
        adapter = CMDBAdapter()
        ip_param = ",".join(ip_list)
        result = adapter.query_devices(
            filters={"mgmt_ip__in": ip_param},
            fields=fields,
            page=1,
            page_size=min(max(pageSize, len(ip_list)), 100),
        )

    request_id = write_audit({
        "user": "api_user",
        "question": "api_query_devices_by_ips",
        "intent": "query_devices_by_ips",
        "tool_name": "query_devices_by_ips",
        "tool_args": {"ips_count": len(ip_list), "fields": fields, "pageSize": pageSize},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    result["answer"] = build_answer(result)
    return result


@app.get("/api/v1/cmdb/explore/mgmt-ip-list")
def explore_mgmt_ip_list(ip: str = Query(...)) -> dict[str, Any]:
    adapter = CMDBAdapter()
    result = adapter.explore_mgmt_ip_list(ip)
    request_id = write_audit({
        "user": "api_user",
        "question": "api_explore_mgmt_ip_list",
        "intent": "explore_mgmt_ip_list",
        "tool_name": "explore_mgmt_ip_list",
        "tool_args": {"ip": ip},
        "data_source": "fund_cmdb_networkServer",
        "result_count": 0,
        "returned_count": 0,
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    return result



@app.get("/api/v1/cmdb/devices/export.xlsx")
def export_devices_xlsx(request: Request):
    import json
    import re as _re
    from urllib.parse import quote

    from fastapi import HTTPException
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from netaiops_asset.cmdb.field_map import QUERY_FILTER_FIELDS, normalize_field_name

    raw_params = dict(request.query_params)

    fields = raw_params.get("fields")

    try:
        page_size = int(raw_params.get("pageSize") or raw_params.get("page_size") or 500)
    except Exception:
        page_size = 500
    page_size = max(1, min(page_size, 500))

    query_alias = {
        "IDC": "IDC__icontains",
        "server_room": "server_room__icontains",
        "rack": "rack__icontains",
        "host_name": "host_name__icontains",
        "sn": "sn__icontains",
        "ci_type": "ci_type__icontains",
        "manufacturer": "manufacturer__icontains",
        "band": "band__icontains",
        "device_spec": "device_spec__icontains",
        "os_version": "os_version__icontains",
        "status": "status__icontains",
        "tag": "tag__icontains",
        "maintenance_manufacturer": "maintenance_manufacturer__icontains",
        "server_ID": "server_ID__icontains",
        "comment": "comment__icontains",
        "oa_contract": "oa_contract__icontains",
        "costcontrol_ticket_id": "costcontrol_ticket_id__icontains",
    }

    skip_keys = {"fields", "pageSize", "page_size", "page"}
    filters = {}

    for key, value in request.query_params.multi_items():
        if key in skip_keys or value in (None, ""):
            continue

        if key == "mgmt_ip":
            filters["mgmt_ip"] = value
            continue

        if key == "search":
            filters["search"] = value
            continue

        if key in query_alias:
            filters[query_alias[key]] = value
            continue

        normalized = normalize_field_name(key)
        if normalized in QUERY_FILTER_FIELDS:
            filters[normalized] = value
            continue

        if "__" in normalized:
            base = normalized.split("__", 1)[0]
            if base in QUERY_FILTER_FIELDS:
                filters[normalized] = value
                continue

    adapter = CMDBAdapter()
    result = adapter.query_devices(filters=filters, fields=fields, page=1, page_size=page_size)

    if result.get("status") != "ok":
        raise HTTPException(
            status_code=502,
            detail={
                "message": "CMDB export query failed",
                "error_code": result.get("error_code"),
                "cmdb_message": result.get("message"),
                "http_status": result.get("http_status"),
            },
        )

    columns = result.get("fields") or normalize_fields(fields)
    labels = result.get("field_labels") or field_labels()
    rows = result.get("items", [])

    def cell_value(value):
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    wb = Workbook()
    ws = wb.active
    ws.title = "CMDB网络设备"

    header_fill = PatternFill("solid", fgColor="F2F2F2")
    header_font = Font(bold=True)
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append([labels.get(c, c) for c in columns])
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row in rows:
        ws.append([cell_value(row.get(c, "")) for c in columns])

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="top")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for idx, col in enumerate(columns, start=1):
        letter = get_column_letter(idx)
        label = labels.get(col, col)
        max_len = len(str(label))
        for row in rows[:200]:
            value = cell_value(row.get(col, ""))
            max_len = max(max_len, len(str(value)) if value is not None else 0)
        ws.column_dimensions[letter].width = min(max(max_len + 2, 12), 42)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    request_id = write_audit({
        "user": "api_user",
        "question": "api_export_devices_xlsx",
        "intent": "export_devices_xlsx",
        "tool_name": "export_devices_xlsx",
        "tool_args": {"filters": filters, "fields": fields, "pageSize": page_size},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })

    headers = {"Content-Disposition": f'attachment; filename="netaiops_cmdb_devices_{request_id}.xlsx"'}
    return StreamingResponse(
        iter([bio.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.get("/api/v1/audit/recent")
def audit_recent(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    import json

    config = get_config()
    audit_dir = Path(config.get("runtime", {}).get("audit_dir", "/var/lib/netaiops-asset-agent/data/audit"))

    items: list[dict[str, Any]] = []
    if audit_dir.exists():
        for f in sorted(audit_dir.glob("audit_*.jsonl"), reverse=True)[:10]:
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    items.append(event)
                except Exception:
                    continue
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

    return {"status": "ok", "count": len(items), "items": items[:limit]}


@app.get("/api/v1/selfcheck")
def selfcheck() -> dict[str, Any]:
    config = get_config()
    cmdb = config.get("cmdb", {})
    runtime = config.get("runtime", {})

    audit_dir = Path(runtime.get("audit_dir", "/var/lib/netaiops-asset-agent/data/audit"))
    export_dir = Path(runtime.get("export_dir", "/var/lib/netaiops-asset-agent/data/exports"))
    conversation_dir = Path(runtime.get("conversation_dir", "/var/lib/netaiops-asset-agent/data/conversations"))

    token_env = cmdb.get("api_token_env", "NETAIOPS_CMDB_API_TOKEN")
    token_configured = bool(os.getenv(token_env, ""))

    def writable(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=str(path), delete=True, encoding="utf-8") as f:
                f.write("ok")
            return True
        except Exception:
            return False

    excel_ok = False
    try:
        import openpyxl  # noqa: F401
        excel_ok = True
    except Exception:
        excel_ok = False

    adapter = CMDBAdapter()
    probe = adapter.probe()
    cmdb_ok = probe.get("status") == "ok" and int(probe.get("http_status") or 0) == 200

    checks = {
        "config_loaded": bool(config),
        "token_configured": token_configured,
        "cmdb_api_reachable": cmdb_ok,
        "audit_dir_writable": writable(audit_dir),
        "export_dir_writable": writable(export_dir),
        "conversation_dir_writable": writable(conversation_dir),
        "excel_export_available": excel_ok,
    }

    overall = "ok" if all(checks.values()) else "warn"

    return {
        "status": overall,
        "service": APP_NAME,
        "version": APP_VERSION,
        "cmdb": {
            "mode": cmdb.get("mode"),
            "env": cmdb.get("env"),
            "api_base_url": cmdb.get("api_base_url"),
            "network_server_path": cmdb.get("network_server_path"),
            "token_env": token_env,
            "token_configured": token_configured,
        },
        "limits": config.get("limits", {}),
        "runtime": {
            "audit_dir": str(audit_dir),
            "export_dir": str(export_dir),
            "conversation_dir": str(conversation_dir),
            "audit_retention_days": runtime.get("audit_retention_days", 90),
            "conversation_retention_days": runtime.get("conversation_retention_days", 180),
        },
        "checks": checks,
        "cmdb_probe": {
            "status": probe.get("status"),
            "http_status": probe.get("http_status"),
            "count": probe.get("count"),
            "code": probe.get("code"),
            "message": probe.get("message"),
        },
    }



@app.get("/api/v1/llm/config")
def api_llm_config() -> dict[str, Any]:
    client = LLMClient()
    return {
        "status": "ok",
        "llm": client.masked_config(),
    }


@app.get("/api/v1/llm/models")
def api_llm_models() -> dict[str, Any]:
    client = LLMClient()
    result = client.list_models()
    write_audit({
        "user": "api_user",
        "question": "api_llm_models",
        "intent": "llm_models",
        "tool_name": "llm_models",
        "tool_args": {"model": client.model, "base_url": client.base_url},
        "data_source": "llm_gateway",
        "result_count": 1 if result.get("status") == "ok" else 0,
        "returned_count": 1 if result.get("status") == "ok" else 0,
        "status": result.get("status", "unknown"),
    })
    return result


@app.get("/api/v1/llm/probe")
def api_llm_probe() -> dict[str, Any]:
    client = LLMClient()
    result = client.probe()
    write_audit({
        "user": "api_user",
        "question": "api_llm_probe",
        "intent": "llm_probe",
        "tool_name": "llm_probe",
        "tool_args": {"model": client.model, "base_url": client.base_url},
        "data_source": "llm_gateway",
        "result_count": 1 if result.get("status") == "ok" else 0,
        "returned_count": 1 if result.get("status") == "ok" else 0,
        "status": result.get("status", "unknown"),
    })
    return result


@app.post("/api/v1/llm/parse")
def api_llm_parse(req: LLMParseRequest) -> dict[str, Any]:
    rule_parsed = parse_question(req.question)
    plan_result = plan_with_llm(req.question, rule_parsed=rule_parsed)
    parsed = apply_llm_plan(plan_result)

    write_audit({
        "user": req.user,
        "question": req.question,
        "intent": "llm_parse",
        "tool_name": "llm_tool_planner",
        "tool_args": {"rule_parsed": rule_parsed},
        "data_source": "llm_gateway",
        "result_count": 1 if plan_result.get("status") == "ok" else 0,
        "returned_count": 1 if parsed else 0,
        "status": plan_result.get("status", "unknown"),
    })

    return {
        "status": "ok" if parsed else "error",
        "question": req.question,
        "rule_parsed": rule_parsed,
        "llm_plan_result": plan_result,
        "parsed": parsed,
    }



@app.post("/api/v1/llm/compare")
def api_llm_compare(req: LLMCompareRequest) -> dict[str, Any]:
    llm_cfg = get_config().get("llm", {})
    rule_parsed = parse_question(req.question)

    should_try, should_try_reason = should_try_llm(
        req.question,
        rule_parsed,
        req.planner_mode,
        llm_cfg,
    )

    llm_plan_result = None
    llm_parsed = None
    accepted = False
    accept_reason = "llm_not_tried"
    selected_parsed = rule_parsed
    planner_source = "rule_parser"

    if should_try:
        llm_plan_result = plan_with_llm(req.question, rule_parsed=rule_parsed)
        llm_parsed = apply_llm_plan(llm_plan_result)
        accepted, accept_reason = accept_llm_parse(
            rule_parsed,
            llm_parsed,
            llm_plan_result,
            req.planner_mode,
            llm_cfg,
        )
        if accepted and llm_parsed:
            selected_parsed = llm_parsed
            planner_source = "llm_tool_planner"

    diagnostics = build_planner_diagnostics(
        requested_mode=req.planner_mode,
        should_try=should_try,
        should_try_reason=should_try_reason,
        accepted=accepted,
        accept_reason=accept_reason,
        rule_parsed=rule_parsed,
        llm_plan_result=llm_plan_result,
    )

    write_audit({
        "user": req.user,
        "question": req.question,
        "intent": "llm_compare",
        "tool_name": "llm_tool_planner",
        "tool_args": {"planner_mode": req.planner_mode, "diagnostics": diagnostics},
        "data_source": "llm_gateway",
        "result_count": 1 if llm_plan_result and llm_plan_result.get("status") == "ok" else 0,
        "returned_count": 1 if selected_parsed else 0,
        "status": "ok",
    })

    return {
        "status": "ok",
        "question": req.question,
        "planner_source": planner_source,
        "diagnostics": diagnostics,
        "rule_parsed": rule_parsed,
        "llm_plan_result": llm_plan_result,
        "llm_parsed": llm_parsed,
        "selected_parsed": selected_parsed,
    }


@app.get("/api/v1/conversations")
def api_list_conversations(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    items = list_conversations(limit=limit)
    return {"status": "ok", "count": len(items), "items": items}


@app.post("/api/v1/conversations")
def api_create_conversation(req: ConversationCreateRequest) -> dict[str, Any]:
    conv = create_conversation(title=req.title or "新对话", user=req.user)
    request_id = write_audit({
        "user": req.user or "web_user",
        "question": "api_create_conversation",
        "intent": "create_conversation",
        "tool_name": "create_conversation",
        "tool_args": {"conversation_id": conv.get("conversation_id")},
        "data_source": "conversation_store",
        "result_count": 1,
        "returned_count": 1,
        "status": "ok",
    })
    conv["request_id"] = request_id
    return {"status": "ok", "conversation": conv}


@app.get("/api/v1/conversations/{conversation_id}")
def api_get_conversation(conversation_id: str) -> dict[str, Any]:
    conv = get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"status": "ok", "conversation": conv}


@app.delete("/api/v1/conversations/{conversation_id}")
def api_delete_conversation(conversation_id: str) -> dict[str, Any]:
    ok = delete_conversation(conversation_id)
    write_audit({
        "user": "web_user",
        "question": "api_delete_conversation",
        "intent": "delete_conversation",
        "tool_name": "delete_conversation",
        "tool_args": {"conversation_id": conversation_id},
        "data_source": "conversation_store",
        "result_count": 1 if ok else 0,
        "returned_count": 1 if ok else 0,
        "status": "ok" if ok else "not_found",
    })
    return {"status": "ok" if ok else "not_found", "deleted": ok}



@app.get("/api/v1/tools/catalog")
def api_tools_catalog() -> dict[str, Any]:
    return {
        "status": "ok",
        "count": len(CMDB_TOOL_CATALOG),
        "items": CMDB_TOOL_CATALOG,
    }


@app.post("/api/v1/tools/cmdb/query")
def api_tool_cmdb_query(req: ToolQueryDevicesRequest) -> dict[str, Any]:
    result = tool_query_cmdb_devices(
        filters=req.filters or {},
        fields=req.fields,
        page=req.page or 1,
        page_size=req.page_size or 20,
    )
    request_id = write_audit({
        "user": req.user,
        "question": "tool_query_cmdb_devices",
        "intent": "tool_query_cmdb_devices",
        "tool_name": "query_cmdb_devices",
        "tool_args": {"filters": req.filters or {}, "fields": req.fields, "page": req.page, "page_size": req.page_size},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    result["answer"] = build_answer(result)
    return result


@app.post("/api/v1/tools/cmdb/detail")
def api_tool_cmdb_detail(req: ToolQueryDeviceDetailRequest) -> dict[str, Any]:
    result = tool_query_cmdb_device_detail(
        keyword=req.keyword,
        fields=req.fields,
    )
    request_id = write_audit({
        "user": req.user,
        "question": "tool_query_cmdb_device_detail",
        "intent": "tool_query_cmdb_device_detail",
        "tool_name": "query_cmdb_device_detail",
        "tool_args": {"keyword": req.keyword, "fields": req.fields},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    result["answer"] = build_answer(result)
    return result


@app.post("/api/v1/tools/cmdb/query-by-ips")
def api_tool_cmdb_query_by_ips(req: ToolQueryDevicesByIpsRequest) -> dict[str, Any]:
    result = tool_query_cmdb_devices_by_ips(
        ips=req.ips,
        fields=req.fields,
        page_size=req.page_size,
    )
    request_id = write_audit({
        "user": req.user,
        "question": "tool_query_cmdb_devices_by_ips",
        "intent": "tool_query_cmdb_devices_by_ips",
        "tool_name": "query_cmdb_devices_by_ips",
        "tool_args": {"ips_count": len(req.ips or []), "fields": req.fields, "page_size": req.page_size},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })
    result["request_id"] = request_id
    result["answer"] = build_answer(result)
    return result


@app.post("/api/v1/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    action_result = handle_conversation_action(req.question, req.conversation_id)
    if action_result is not None:
        request_id = write_audit({
            "user": req.user,
            "question": req.question,
            "intent": "conversation_action",
            "tool_name": action_result.get("action"),
            "tool_args": {
                "conversation_id": req.conversation_id,
                "export_params": action_result.get("export_params"),
                "source_turn_id": action_result.get("source_turn_id"),
            },
            "data_source": "conversation_store",
            "result_count": action_result.get("count", 0),
            "returned_count": action_result.get("returned", 0),
            "status": action_result.get("status", "unknown"),
        })

        response = {
            "status": action_result.get("status", "ok"),
            "request_id": request_id,
            "question": req.question,
            "parsed": {
                "intent": "conversation_action",
                "action": action_result.get("action"),
                "reason": "conversation_action_detector",
            },
            "llm_plan": None,
            "planner_source": "conversation_action",
            "planner_diagnostics": None,
            "action": action_result.get("action"),
            "answer": action_result.get("answer"),
            "columns": action_result.get("columns", []),
            "field_labels": action_result.get("field_labels", field_labels()),
            "count": action_result.get("count", 0),
            "returned": action_result.get("returned", 0),
            "items": action_result.get("items", []),
            "export_url": action_result.get("export_url"),
            "export_params": action_result.get("export_params"),
            "source_turn_id": action_result.get("source_turn_id"),
        }

        cid, _ = append_turn(req.conversation_id, req.question, response, user=req.user)
        response["conversation_id"] = cid
        return response

    llm_cfg = get_config().get("llm", {})

    rule_parsed = parse_question(req.question)
    parsed = rule_parsed

    llm_plan_result = None
    llm_parsed = None
    planner_source = "rule_parser"

    should_try, should_try_reason = should_try_llm(
        req.question,
        rule_parsed,
        req.planner_mode,
        llm_cfg,
    )

    accepted = False
    accept_reason = "llm_not_tried"

    if should_try:
        llm_plan_result = plan_with_llm(req.question, rule_parsed=rule_parsed)
        llm_parsed = apply_llm_plan(llm_plan_result)
        accepted, accept_reason = accept_llm_parse(
            rule_parsed,
            llm_parsed,
            llm_plan_result,
            req.planner_mode,
            llm_cfg,
        )

        if accepted and llm_parsed:
            parsed = llm_parsed
            planner_source = "llm_tool_planner"

    planner_diagnostics = build_planner_diagnostics(
        requested_mode=req.planner_mode,
        should_try=should_try,
        should_try_reason=should_try_reason,
        accepted=accepted,
        accept_reason=accept_reason,
        rule_parsed=rule_parsed,
        llm_plan_result=llm_plan_result,
    )

    if parsed.get("intent") == "clarify":
        request_id = write_audit({
            "user": req.user,
            "question": req.question,
            "intent": "clarify",
            "tool_name": "llm_tool_planner" if planner_source == "llm_tool_planner" else None,
            "tool_args": {"parsed": parsed, "planner_diagnostics": planner_diagnostics},
            "data_source": "llm_gateway" if planner_source == "llm_tool_planner" else None,
            "result_count": 0,
            "returned_count": 0,
            "status": "need_clarification",
        })
        response = {
            "status": "need_clarification",
            "request_id": request_id,
            "question": req.question,
            "parsed": parsed,
            "llm_plan": llm_plan_result,
            "planner_source": planner_source,
            "planner_diagnostics": planner_diagnostics if req.debug else None,
            "answer": parsed.get("message"),
            "items": [],
            "columns": [],
            "field_labels": field_labels(),
            "count": 0,
            "returned": 0,
        }
        cid, _ = append_turn(req.conversation_id, req.question, response, user=req.user)
        response["conversation_id"] = cid
        return response

    if parsed.get("intent") == "query_device_detail":
        result = tool_query_cmdb_device_detail(
            keyword=parsed.get("keyword", ""),
            fields=parsed.get("fields"),
        )
        tool_name = "query_cmdb_device_detail"
        tool_args = {"keyword": parsed.get("keyword"), "fields": parsed.get("fields")}
    else:
        limit = max(1, min(int(req.limit or 20), 100))
        result = tool_query_cmdb_devices(
            filters=parsed.get("filters", {}),
            fields=parsed.get("fields"),
            page=1,
            page_size=limit,
        )
        tool_name = "query_cmdb_devices"
        tool_args = {"filters": parsed.get("filters", {}), "fields": parsed.get("fields"), "limit": limit}

    request_id = write_audit({
        "user": req.user,
        "question": req.question,
        "intent": parsed.get("intent"),
        "tool_name": tool_name,
        "tool_args": {"tool_args": tool_args, "planner_source": planner_source, "planner_diagnostics": planner_diagnostics},
        "data_source": "fund_cmdb_networkServer",
        "result_count": result.get("count", 0),
        "returned_count": result.get("returned", 0),
        "status": result.get("status", "unknown"),
    })

    response = {
        "status": result.get("status", "ok"),
        "request_id": request_id,
        "question": req.question,
        "parsed": parsed,
        "llm_plan": llm_plan_result,
        "planner_source": planner_source,
        "planner_diagnostics": planner_diagnostics if req.debug else None,
        "answer": build_answer(result),
        "columns": result.get("fields", normalize_fields(None)),
        "field_labels": result.get("field_labels", field_labels()),
        "count": result.get("count", 0),
        "returned": result.get("returned", 0),
        "items": result.get("items", []),
    }

    cid, _ = append_turn(req.conversation_id, req.question, response, user=req.user)
    response["conversation_id"] = cid
    return response
