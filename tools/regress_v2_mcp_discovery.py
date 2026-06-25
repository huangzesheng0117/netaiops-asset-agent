#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch33 MCP discovery regression.

This script verifies:
- Netmiko MCP SSE / JSON-RPC initialize
- Prometheus MCP SSE / JSON-RPC initialize
- tools/list for both MCP servers
- safe tools/call:
  - Netmiko: get_network_device_list
  - Prometheus: health_check, list_metrics, execute_query count(up)
- Prometheus get_targets known limitation through VictoriaMetrics

It does NOT execute any network device CLI command.
It does NOT call Netmiko send_command_and_get_output.
It does NOT call Netmiko set_config_commands_and_commit_or_save.
"""

from __future__ import print_function

import json
import queue
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


NETMIKO_SSE = "http://10.191.97.137:10000/sse"
PROMETHEUS_SSE = "http://10.191.97.137:10001/sse"
PROMETHEUS_DIRECT = "http://10.191.96.43:9090"


class McpSession(object):
    def __init__(self, name, sse_url):
        self.name = name
        self.sse_url = sse_url
        self.base_url = sse_url.rsplit("/sse", 1)[0] + "/"
        self.endpoint_url = None
        self.endpoint_queue = queue.Queue()
        self.message_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None
        self.rpc_id = 1

    def start(self):
        self.thread = threading.Thread(target=self._reader)
        self.thread.daemon = True
        self.thread.start()

        self.endpoint_url = self._wait_endpoint(timeout=8)

        init = self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "netaiops-asset-agent-v2-mcp-discovery-regress",
                "version": "0.1.0",
            },
        }, timeout=12)

        self.notify("notifications/initialized", {})
        return init

    def stop(self):
        self.stop_event.set()
        time.sleep(0.2)

    def _reader(self):
        event_name = None
        data_lines = []

        try:
            req = urllib.request.Request(
                self.sse_url,
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                while not self.stop_event.is_set():
                    raw = resp.readline()
                    if not raw:
                        break

                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")

                    if line == "":
                        if event_name or data_lines:
                            data = "\n".join(data_lines)
                            if event_name == "endpoint":
                                self.endpoint_queue.put(data.strip())
                            else:
                                try:
                                    self.message_queue.put(json.loads(data))
                                except Exception:
                                    self.message_queue.put({
                                        "_event": event_name,
                                        "_raw_data": data[:1000],
                                    })

                        event_name = None
                        data_lines = []
                        continue

                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line.split(":", 1)[1].lstrip())

        except Exception as e:
            self.message_queue.put({
                "_reader_error": repr(e),
            })

    def _wait_endpoint(self, timeout):
        endpoint = self.endpoint_queue.get(timeout=timeout)
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        return urllib.parse.urljoin(self.base_url, endpoint.lstrip("/"))

    def _post(self, payload, timeout):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(800).decode("utf-8", errors="replace")
                return {
                    "ok": True,
                    "status": resp.status,
                    "body": body,
                }
        except urllib.error.HTTPError as e:
            body = e.read(800).decode("utf-8", errors="replace")
            return {
                "ok": False,
                "status": e.code,
                "body": body,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": repr(e),
            }

    def _wait_response(self, rpc_id, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.message_queue.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break

            if isinstance(msg, dict) and msg.get("id") == rpc_id:
                return msg

        return None

    def request(self, method, params, timeout=15):
        rpc_id = self.rpc_id
        self.rpc_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        }

        post = self._post(payload, timeout=timeout)
        response = self._wait_response(rpc_id, timeout=timeout)

        return {
            "rpc_id": rpc_id,
            "method": method,
            "post": post,
            "response": response,
        }

    def notify(self, method, params):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        return self._post(payload, timeout=8)

    def list_tools(self):
        return self.request("tools/list", {}, timeout=12)

    def call_tool(self, name, arguments, timeout=25):
        return self.request("tools/call", {
            "name": name,
            "arguments": arguments,
        }, timeout=timeout)


def tool_response_text(call_result):
    response = call_result.get("response")
    if not response:
        return None, True, "no json-rpc response"

    if "error" in response:
        return None, True, json.dumps(response.get("error"), ensure_ascii=False)

    result = response.get("result") or {}
    is_error = bool(result.get("isError"))

    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return first.get("text", ""), is_error, None
        return str(first), is_error, None

    return json.dumps(result, ensure_ascii=False), is_error, None


def parse_json_text(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def warn(message):
    print("[WARN]", message)


def direct_prometheus_targets_probe():
    url = PROMETHEUS_DIRECT.rstrip("/") + "/api/v1/targets?state=active"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read(2000000).decode("utf-8", errors="replace")
            data = json.loads(body)
            active = data.get("data", {}).get("activeTargets", [])
            return {
                "ok": True,
                "status": resp.status,
                "active_targets_count": len(active),
            }
    except Exception as e:
        return {
            "ok": False,
            "error": repr(e),
        }


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "netmiko": {},
        "prometheus": {},
        "direct_prometheus_targets": {},
        "errors": errors,
    }

    netmiko = None
    prometheus = None

    try:
        print("========== V2 MCP Discovery Regression ==========")

        print("\n========== Init Netmiko MCP ==========")
        netmiko = McpSession("netmiko_mcp", NETMIKO_SSE)
        netmiko_init = netmiko.start()
        report["netmiko"]["initialize"] = netmiko_init
        require(
            bool(netmiko_init.get("response") and "result" in netmiko_init["response"]),
            "Netmiko MCP initialize success",
            errors,
        )

        print("\n========== Init Prometheus MCP ==========")
        prometheus = McpSession("prometheus_mcp", PROMETHEUS_SSE)
        prometheus_init = prometheus.start()
        report["prometheus"]["initialize"] = prometheus_init
        require(
            bool(prometheus_init.get("response") and "result" in prometheus_init["response"]),
            "Prometheus MCP initialize success",
            errors,
        )

        print("\n========== tools/list ==========")
        netmiko_tools = netmiko.list_tools()
        prometheus_tools = prometheus.list_tools()
        report["netmiko"]["tools_list"] = netmiko_tools
        report["prometheus"]["tools_list"] = prometheus_tools

        nt_resp = netmiko_tools.get("response") or {}
        pt_resp = prometheus_tools.get("response") or {}

        nt_tools = [
            t.get("name") for t in
            ((nt_resp.get("result") or {}).get("tools") or [])
        ]
        pt_tools = [
            t.get("name") for t in
            ((pt_resp.get("result") or {}).get("tools") or [])
        ]

        print("Netmiko tools:", ", ".join(nt_tools))
        print("Prometheus tools:", ", ".join(pt_tools))

        require("get_network_device_list" in nt_tools, "Netmiko has get_network_device_list", errors)
        require("send_command_and_get_output" in nt_tools, "Netmiko has send_command_and_get_output", errors)
        require("set_config_commands_and_commit_or_save" in nt_tools, "Netmiko config tool discovered and must be blocked by ChatBot", errors)

        require("health_check" in pt_tools, "Prometheus has health_check", errors)
        require("execute_query" in pt_tools, "Prometheus has execute_query", errors)
        require("execute_range_query" in pt_tools, "Prometheus has execute_range_query", errors)
        require("list_metrics" in pt_tools, "Prometheus has list_metrics", errors)

        print("\n========== Safe tools/call ==========")

        netmiko_list = netmiko.call_tool("get_network_device_list", {}, timeout=30)
        text, is_error, err = tool_response_text(netmiko_list)
        report["netmiko"]["get_network_device_list"] = {
            "is_error": is_error,
            "error": err,
            "text_len": len(text or ""),
        }
        devices = parse_json_text(text)
        device_count = len(devices) if isinstance(devices, list) else None
        report["netmiko"]["get_network_device_list"]["device_count"] = device_count
        require(not is_error and device_count is not None and device_count > 0, "Netmiko get_network_device_list returns device list", errors)
        print("Netmiko device_count:", device_count)

        prom_health = prometheus.call_tool("health_check", {}, timeout=20)
        text, is_error, err = tool_response_text(prom_health)
        health_json = parse_json_text(text)
        report["prometheus"]["health_check"] = {
            "is_error": is_error,
            "error": err,
            "parsed": health_json,
        }
        require(not is_error and isinstance(health_json, dict) and health_json.get("status") == "healthy", "Prometheus health_check healthy", errors)

        prom_metrics = prometheus.call_tool("list_metrics", {
            "limit": 10,
            "offset": 0,
            "refresh_cache": False,
        }, timeout=30)
        text, is_error, err = tool_response_text(prom_metrics)
        metrics_json = parse_json_text(text)
        report["prometheus"]["list_metrics"] = {
            "is_error": is_error,
            "error": err,
            "parsed": metrics_json,
        }
        require(
            not is_error and isinstance(metrics_json, dict) and metrics_json.get("returned_count", 0) > 0,
            "Prometheus list_metrics returns metrics",
            errors,
        )
        if isinstance(metrics_json, dict):
            print("Prometheus metrics total_count:", metrics_json.get("total_count"))
            print("Prometheus metrics returned_count:", metrics_json.get("returned_count"))

        prom_query = prometheus.call_tool("execute_query", {
            "query": "count(up)",
        }, timeout=30)
        text, is_error, err = tool_response_text(prom_query)
        query_json = parse_json_text(text)
        report["prometheus"]["execute_query_count_up"] = {
            "is_error": is_error,
            "error": err,
            "parsed": query_json,
        }
        require(
            not is_error and isinstance(query_json, dict) and query_json.get("resultType") == "vector",
            "Prometheus execute_query count(up) returns vector",
            errors,
        )

        print("\n========== Expected limitation: get_targets via VictoriaMetrics ==========")
        prom_targets = prometheus.call_tool("get_targets", {}, timeout=20)
        text, is_error, err = tool_response_text(prom_targets)
        report["prometheus"]["get_targets_via_mcp"] = {
            "is_error": is_error,
            "error": err,
            "text": (text or "")[:1000],
        }
        if is_error:
            warn("Prometheus MCP get_targets returned isError=true; expected when backend is VictoriaMetrics.")
        else:
            warn("Prometheus MCP get_targets unexpectedly succeeded; verify result manually.")

        print("\n========== Direct Prometheus targets probe ==========")
        direct_targets = direct_prometheus_targets_probe()
        report["direct_prometheus_targets"] = direct_targets
        if direct_targets.get("ok"):
            print("[OK] Direct Prometheus targets probe success, active_targets_count={}".format(
                direct_targets.get("active_targets_count")
            ))
        else:
            warn("Direct Prometheus targets probe failed: {}".format(direct_targets.get("error")))

    except Exception as e:
        errors.append(repr(e))
        report["fatal_error"] = repr(e)
        report["traceback"] = traceback.format_exc()
        print("[FATAL]", repr(e))
        print(traceback.format_exc())
    finally:
        if netmiko:
            netmiko.stop()
        if prometheus:
            prometheus.stop()

    out = "/tmp/v2_mcp_discovery_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== Result ==========")
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
