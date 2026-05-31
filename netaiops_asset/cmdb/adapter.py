from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urljoin

import requests

from netaiops_asset.cmdb.field_map import (
    DEFAULT_FIELDS,
    DETAIL_FIELDS,
    QUERY_FILTER_FIELDS,
    field_labels,
    normalize_field_name,
    normalize_fields,
)
from netaiops_asset.config_loader import get_config


class CMDBAdapter:
    def __init__(self) -> None:
        self.config = get_config()
        self.cmdb_config = self.config.get("cmdb", {})
        self.mode = self.cmdb_config.get("mode", "api")
        self.base_url = self.cmdb_config.get("api_base_url") or os.getenv("NETAIOPS_CMDB_BASE_URL", "")
        self.path = self.cmdb_config.get("network_server_path", "/fund_cmdb2/networkServer/")
        self.token_env = self.cmdb_config.get("api_token_env", "NETAIOPS_CMDB_API_TOKEN")
        self.token = os.getenv(self.token_env, "")
        self.default_page_size = int(self.cmdb_config.get("default_page_size", 20) or 20)
        self.max_page_size = int(self.cmdb_config.get("max_page_size", 500) or 500)
        self.timeout = int(self.cmdb_config.get("request_timeout", 15) or 15)
        self.retries = int(self.cmdb_config.get("request_retries", 2) or 2)
        self.backoff = float(self.cmdb_config.get("retry_backoff_seconds", 0.8) or 0.8)
        self.local_filter_enabled = bool(self.cmdb_config.get("local_filter_enabled", True))
        self.local_filter_scan_page_size = int(self.cmdb_config.get("local_filter_scan_page_size", 500) or 500)
        self.local_filter_max_scan_rows = int(self.cmdb_config.get("local_filter_max_scan_rows", 2000) or 2000)

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-JWT-TOKEN": self.token}

    def _url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", self.path.lstrip("/"))

    @staticmethod
    def _safe_result_row(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        return {field: row.get(field, None) for field in fields}

    @staticmethod
    def _parse_response(payload: Any) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
        if not isinstance(payload, dict):
            return 0, [], {"raw_type": type(payload).__name__}

        if isinstance(payload.get("results"), list):
            return int(payload.get("count") or len(payload.get("results") or [])), payload.get("results") or [], payload

        if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("results"), list):
            data = payload["data"]
            return int(data.get("count") or len(data.get("results") or [])), data.get("results") or [], payload

        if isinstance(payload.get("data"), list):
            return len(payload.get("data") or []), payload.get("data") or [], payload

        if isinstance(payload.get("list"), list):
            return len(payload.get("list") or []), payload.get("list") or [], payload

        return 0, [], payload

    @staticmethod
    def _http_error_code(status_code: int) -> str:
        if status_code == 401:
            return "CMDB_AUTH_REQUIRED"
        if status_code == 403:
            return "CMDB_FORBIDDEN"
        if status_code == 404:
            return "CMDB_NOT_FOUND"
        if status_code >= 500:
            return "CMDB_SERVER_ERROR"
        return "CMDB_HTTP_ERROR"

    def _get_once(self, params: dict[str, Any]) -> dict[str, Any]:
        res = requests.get(self._url(), headers=self._headers(), params=params, timeout=self.timeout)
        status_code = res.status_code

        try:
            payload = res.json()
        except Exception:
            return {
                "status": "error",
                "error_code": "CMDB_NON_JSON_RESPONSE",
                "http_status": status_code,
                "message": "CMDB response is not JSON",
                "text_preview": res.text[:300],
            }

        if status_code >= 400:
            return {
                "status": "error",
                "error_code": self._http_error_code(status_code),
                "http_status": status_code,
                "message": f"CMDB HTTP error: {status_code}",
                "payload_preview": payload if isinstance(payload, dict) else str(payload)[:300],
            }

        count, rows, raw = self._parse_response(payload)
        return {
            "status": "ok",
            "http_status": status_code,
            "count": count,
            "rows": rows,
            "raw_keys": list(raw.keys()) if isinstance(raw, dict) else [],
            "next": raw.get("next") if isinstance(raw, dict) else None,
            "previous": raw.get("previous") if isinstance(raw, dict) else None,
            "code": raw.get("code") if isinstance(raw, dict) else None,
        }

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "error", "error_code": "CMDB_EMPTY_BASE_URL", "message": "CMDB api_base_url is empty"}
        if not self.token:
            return {"status": "error", "error_code": "CMDB_EMPTY_TOKEN", "message": f"CMDB token env {self.token_env} is empty"}

        last_error: dict[str, Any] | None = None
        attempts = max(1, self.retries + 1)

        for idx in range(attempts):
            try:
                result = self._get_once(params)
                if result.get("status") == "ok":
                    result["retry_attempts"] = idx
                    return result

                last_error = result
                http_status = int(result.get("http_status") or 0)
                if http_status in (401, 403, 404):
                    return result

            except requests.Timeout as e:
                last_error = {
                    "status": "error",
                    "error_code": "CMDB_TIMEOUT",
                    "message": f"CMDB request timeout after {self.timeout}s",
                    "detail": str(e),
                }
            except requests.ConnectionError as e:
                last_error = {
                    "status": "error",
                    "error_code": "CMDB_CONNECTION_ERROR",
                    "message": "CMDB connection error",
                    "detail": str(e),
                }
            except requests.RequestException as e:
                last_error = {
                    "status": "error",
                    "error_code": "CMDB_REQUEST_ERROR",
                    "message": f"CMDB request failed: {type(e).__name__}: {e}",
                }

            if idx < attempts - 1:
                time.sleep(self.backoff * (idx + 1))

        return last_error or {"status": "error", "error_code": "CMDB_UNKNOWN_ERROR", "message": "CMDB request failed"}

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @staticmethod
    def _split_filter_key(key: str) -> tuple[str, str]:
        if "__" in key:
            base, op = key.split("__", 1)
            return normalize_field_name(base), op
        return normalize_field_name(key), "exact"

    @staticmethod
    def _normalize_filter_value(field: str, value: Any) -> str:
        text = str(value or "").strip()

        if field == "rack":
            text = (
                text.upper()
                .replace("排机柜", "")
                .replace("排", "")
                .replace("机柜", "")
                .strip()
            )

        if field == "server_room":
            text = text.replace("机房", "").strip()

        if field == "IDC":
            text = (
                text.upper()
                .replace("机房", "")
                .replace("IDC", "")
                .strip()
            )

        return text

    def _match_one_filter(self, row: dict[str, Any], key: str, expected: Any) -> bool:
        if expected in (None, ""):
            return True

        key = normalize_field_name(key)

        if key == "search":
            needle = str(expected).strip().lower()
            if not needle:
                return True
            return any(needle in self._stringify(v).lower() for v in row.values())

        field, op = self._split_filter_key(key)
        expected_text = self._normalize_filter_value(field, expected)
        actual_text = self._stringify(row.get(field, ""))

        if op == "icontains":
            return expected_text.lower() in actual_text.lower()

        if op == "contains":
            return expected_text in actual_text

        if op == "in":
            values = [x.strip().lower() for x in re.split(r"[,，\s]+", expected_text) if x.strip()]
            return actual_text.strip().lower() in values

        if op in {"exact", "eq"}:
            return actual_text.strip().lower() == expected_text.strip().lower()

        if op == "startswith":
            return actual_text.strip().lower().startswith(expected_text.strip().lower())

        if op == "endswith":
            return actual_text.strip().lower().endswith(expected_text.strip().lower())

        # 未识别操作符时，按 icontains 兜底，避免 CMDB 不支持某些 lookup 时返回脏数据。
        return expected_text.lower() in actual_text.lower()

    def _local_filter_rows(self, rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.local_filter_enabled or not filters:
            return rows

        matched = []
        for row in rows:
            if all(self._match_one_filter(row, key, value) for key, value in filters.items()):
                matched.append(row)

        return matched

    def _build_remote_params(self, filters: dict[str, Any], page: int, page_size: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "page": max(1, int(page or 1)),
            "pageSize": max(1, min(int(page_size or self.default_page_size), self.max_page_size)),
        }

        for k, v in filters.items():
            if v in (None, ""):
                continue

            normalized = normalize_field_name(k)
            if normalized == "search":
                params["search"] = str(v).strip()
            elif normalized in QUERY_FILTER_FIELDS or "__" in normalized:
                params[normalized] = v

        return params

    def _scan_rows(self, params: dict[str, Any], max_rows: int | None = None) -> dict[str, Any]:
        max_rows = max_rows or self.local_filter_max_scan_rows
        scan_page_size = max(1, min(self.local_filter_scan_page_size, self.max_page_size))
        rows: list[dict[str, Any]] = []
        last_result: dict[str, Any] = {}
        max_pages = max(1, (max_rows + scan_page_size - 1) // scan_page_size)

        for page in range(1, max_pages + 1):
            p = dict(params)
            p["page"] = page
            p["pageSize"] = scan_page_size

            result = self._get(p)
            last_result = result

            if result.get("status") != "ok":
                return result

            batch = result.get("rows", []) or []
            rows.extend(batch)

            remote_count = int(result.get("count") or 0)
            if not batch:
                break

            if len(rows) >= max_rows:
                rows = rows[:max_rows]
                break

            if remote_count > 0 and len(rows) >= remote_count:
                break

            if len(batch) < scan_page_size:
                break

        return {
            "status": "ok",
            "http_status": last_result.get("http_status"),
            "count": len(rows),
            "rows": rows,
            "raw_keys": last_result.get("raw_keys", []),
            "next": last_result.get("next"),
            "previous": last_result.get("previous"),
            "code": last_result.get("code"),
            "retry_attempts": last_result.get("retry_attempts", 0),
            "scan_rows": len(rows),
        }

    def probe(self) -> dict[str, Any]:
        params = {"page": 1, "pageSize": 1}
        result = self._get(params)
        rows = result.pop("rows", [])
        first_keys = sorted(list(rows[0].keys())) if rows else []
        result.update({
            "data_source": "fund_cmdb_networkServer",
            "base_url": self.base_url,
            "path": self.path,
            "params": params,
            "first_row_keys": first_keys,
            "first_row_preview": {k: rows[0].get(k) for k in first_keys[:20]} if rows else {},
        })
        return result

    def query_devices(
        self,
        filters: dict[str, Any] | None = None,
        fields: list[str] | str | None = None,
        limit: int | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        selected_fields = normalize_fields(fields, DEFAULT_FIELDS)
        filters = filters or {}

        size = int(page_size or limit or self.default_page_size)
        size = max(1, min(size, self.max_page_size))
        page = max(1, int(page or 1))

        if filters:
            # 有过滤条件时，先尽量把候选集拉足，再由本地严格二次过滤。
            # 这样可以修复 CMDB API 对 env__icontains 等条件不生效的问题。
            remote_params = self._build_remote_params(filters, page=1, page_size=self.local_filter_scan_page_size)
            result = self._scan_rows(remote_params, max_rows=self.local_filter_max_scan_rows)

            if result.get("status") != "ok":
                # 如果某些过滤参数导致 CMDB API 报错，则退回全量候选扫描，再本地过滤。
                fallback_params = {"page": 1, "pageSize": self.local_filter_scan_page_size}
                result = self._scan_rows(fallback_params, max_rows=self.local_filter_max_scan_rows)

            if result.get("status") != "ok":
                return {
                    **result,
                    "data_source": "fund_cmdb_networkServer",
                    "mode": self.mode,
                    "filters": filters,
                    "cmdb_params": remote_params,
                    "fields": selected_fields,
                    "field_labels": field_labels(),
                    "items": [],
                    "returned": 0,
                    "count": 0,
                }

            candidate_rows = result.get("rows", []) or []
            filtered_rows = self._local_filter_rows(candidate_rows, filters)

            start = (page - 1) * size
            end = start + size
            page_rows = filtered_rows[start:end]
            items = [self._safe_result_row(row, selected_fields) for row in page_rows]

            return {
                "status": "ok",
                "data_source": "fund_cmdb_networkServer",
                "mode": self.mode,
                "filters": filters,
                "cmdb_params": remote_params,
                "fields": selected_fields,
                "field_labels": field_labels(),
                "count": len(filtered_rows),
                "returned": len(items),
                "items": items,
                "raw_keys": result.get("raw_keys", []),
                "next": None,
                "previous": None,
                "code": result.get("code"),
                "http_status": result.get("http_status"),
                "retry_attempts": result.get("retry_attempts", 0),
                "local_filter_applied": True,
                "candidate_rows": len(candidate_rows),
                "scan_rows": result.get("scan_rows", len(candidate_rows)),
            }

        params = self._build_remote_params(filters, page=page, page_size=size)
        result = self._get(params)

        if result.get("status") != "ok":
            return {
                **result,
                "data_source": "fund_cmdb_networkServer",
                "mode": self.mode,
                "filters": filters,
                "cmdb_params": params,
                "fields": selected_fields,
                "field_labels": field_labels(),
                "items": [],
                "returned": 0,
                "count": 0,
            }

        rows = result.get("rows", [])
        items = [self._safe_result_row(row, selected_fields) for row in rows]

        return {
            "status": "ok",
            "data_source": "fund_cmdb_networkServer",
            "mode": self.mode,
            "filters": filters,
            "cmdb_params": params,
            "fields": selected_fields,
            "field_labels": field_labels(),
            "count": result.get("count", len(items)),
            "returned": len(items),
            "items": items,
            "raw_keys": result.get("raw_keys", []),
            "next": result.get("next"),
            "previous": result.get("previous"),
            "code": result.get("code"),
            "http_status": result.get("http_status"),
            "retry_attempts": result.get("retry_attempts", 0),
            "local_filter_applied": False,
        }

    def query_device_detail(self, keyword: str, fields: list[str] | str | None = None) -> dict[str, Any]:
        keyword = str(keyword or "").strip()
        selected_fields = normalize_fields(fields, DETAIL_FIELDS)

        if not keyword:
            return {
                "status": "error",
                "error_code": "TOOL_BAD_REQUEST",
                "message": "keyword is required",
                "count": 0,
                "returned": 0,
                "items": [],
            }

        if extract_ip(keyword):
            filters = {"mgmt_ip": keyword}
        else:
            filters = {"search": keyword}

        return self.query_devices(filters=filters, fields=selected_fields, limit=20, page=1, page_size=20)

    def explore_mgmt_ip_list(self, ip: str) -> dict[str, Any]:
        ip = str(ip or "").strip()
        if not ip:
            return {"status": "skipped", "message": "ip is empty"}

        tests = [
            {"name": "mgmt_ip__icontains", "params": {"page": 1, "pageSize": 3, "mgmt_ip__icontains": ip}},
            {"name": "mgmt_ip", "params": {"page": 1, "pageSize": 3, "mgmt_ip": ip}},
            {"name": "mgmt_ip_list_single", "params": {"page": 1, "pageSize": 3, "mgmt_ip_list": ip}},
            {"name": "mgmt_ip__in_single", "params": {"page": 1, "pageSize": 3, "mgmt_ip__in": ip}},
        ]

        results = []
        for t in tests:
            r = self._get(t["params"])
            rows = r.pop("rows", [])
            results.append({
                "name": t["name"],
                "params": t["params"],
                "status": r.get("status"),
                "error_code": r.get("error_code"),
                "http_status": r.get("http_status"),
                "count": r.get("count"),
                "returned": len(rows),
                "first_row_keys": sorted(list(rows[0].keys())) if rows else [],
                "message": r.get("message"),
            })
        return {"status": "ok", "ip": ip, "tests": results}


def extract_ip(text: str) -> str | None:
    m = re.search(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", text or "")
    if not m:
        return None

    ip = m.group(0)
    parts = ip.split(".")
    try:
        if all(0 <= int(x) <= 255 for x in parts):
            return ip
    except ValueError:
        return None
    return None
