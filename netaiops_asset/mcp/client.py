# -*- coding: utf-8 -*-
"""
Generic MCP SSE + JSON-RPC client.

This module intentionally uses Python standard library only, so it does not
depend on external mcp/httpx/httpx_sse packages.

Safety note:
- This is a transport client only.
- It does not decide whether a tool is safe.
- Upper-layer clients must explicitly block dangerous tools.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class McpToolResult:
    ok: bool
    is_error: bool
    server_name: str
    tool_name: str
    content_text: str = ""
    content_json: Any = None
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    endpoint_url: Optional[str] = None
    http_status: Optional[int] = None


class McpClientError(RuntimeError):
    pass


class McpClient:
    def __init__(
        self,
        name: str,
        sse_url: str,
        protocol_version: str = "2024-11-05",
        client_name: str = "netaiops-asset-agent-v2",
        client_version: str = "0.1.0",
        connect_timeout: int = 8,
        request_timeout: int = 20,
        max_text_length: int = 200000,
    ) -> None:
        self.name = name
        self.sse_url = sse_url
        self.protocol_version = protocol_version
        self.client_name = client_name
        self.client_version = client_version
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.max_text_length = max_text_length

        self.base_url = sse_url.rsplit("/sse", 1)[0] + "/"
        self.endpoint_url: Optional[str] = None
        self.rpc_id = 1

        self._endpoint_queue: "queue.Queue[str]" = queue.Queue()
        self._message_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._initialized = False

    def __enter__(self) -> "McpClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> Dict[str, Any]:
        if self._initialized:
            return {"ok": True, "already_initialized": True}

        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

        endpoint = self._wait_endpoint(timeout=self.connect_timeout)
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            self.endpoint_url = endpoint
        else:
            self.endpoint_url = urllib.parse.urljoin(self.base_url, endpoint.lstrip("/"))

        init_result = self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": self.client_name,
                    "version": self.client_version,
                },
            },
            timeout=self.request_timeout,
        )

        response = init_result.get("response")
        if not response or "result" not in response:
            raise McpClientError("MCP initialize failed for {}: {}".format(self.name, init_result))

        self.notify("notifications/initialized", {})
        self._initialized = True
        return init_result

    def close(self) -> None:
        self._stop_event.set()
        time.sleep(0.2)

    def list_tools(self, timeout: Optional[int] = None) -> List[Dict[str, Any]]:
        result = self.request("tools/list", {}, timeout=timeout or self.request_timeout)
        response = result.get("response")
        if not response:
            raise McpClientError("tools/list no response for {}".format(self.name))
        if "error" in response:
            raise McpClientError("tools/list error for {}: {}".format(self.name, response["error"]))

        tools = ((response.get("result") or {}).get("tools") or [])
        if not isinstance(tools, list):
            raise McpClientError("tools/list invalid result for {}".format(self.name))
        return tools

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> McpToolResult:
        result = self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments or {},
            },
            timeout=timeout or self.request_timeout,
        )

        post = result.get("post") or {}
        response = result.get("response")
        content_text, content_json, is_error, err = self._parse_tool_response(response)

        ok = bool(response is not None and err is None and not is_error)

        return McpToolResult(
            ok=ok,
            is_error=is_error,
            server_name=self.name,
            tool_name=tool_name,
            content_text=content_text,
            content_json=content_json,
            raw_response=response,
            error=err,
            endpoint_url=self.endpoint_url,
            http_status=post.get("status"),
        )

    def request(self, method: str, params: Dict[str, Any], timeout: Optional[int] = None) -> Dict[str, Any]:
        if not self.endpoint_url and method != "initialize":
            raise McpClientError("MCP endpoint is not ready for {}".format(self.name))

        rpc_id = self.rpc_id
        self.rpc_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        }

        post = self._post(payload, timeout=timeout or self.request_timeout)
        response = self._wait_response(rpc_id, timeout=timeout or self.request_timeout)

        return {
            "rpc_id": rpc_id,
            "method": method,
            "post": post,
            "response": response,
        }

    def notify(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        return self._post(payload, timeout=self.request_timeout)

    def _reader(self) -> None:
        event_name = None
        data_lines: List[str] = []

        try:
            req = urllib.request.Request(
                self.sse_url,
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                while not self._stop_event.is_set():
                    raw = resp.readline()
                    if not raw:
                        break

                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")

                    if line == "":
                        if event_name or data_lines:
                            data = "\n".join(data_lines)

                            if event_name == "endpoint":
                                self._endpoint_queue.put(data.strip())
                            else:
                                try:
                                    self._message_queue.put(json.loads(data))
                                except Exception:
                                    self._message_queue.put({
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

        except Exception as exc:
            self._message_queue.put({"_reader_error": repr(exc)})

    def _wait_endpoint(self, timeout: int) -> str:
        try:
            return self._endpoint_queue.get(timeout=timeout)
        except queue.Empty:
            raise McpClientError("Timeout waiting MCP endpoint for {}".format(self.name))

    def _post(self, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        if not self.endpoint_url:
            raise McpClientError("MCP endpoint_url is empty for {}".format(self.name))

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
                body = resp.read(1200).decode("utf-8", errors="replace")
                return {
                    "ok": True,
                    "status": resp.status,
                    "body": body,
                }
        except urllib.error.HTTPError as exc:
            body = exc.read(1200).decode("utf-8", errors="replace")
            return {
                "ok": False,
                "status": exc.code,
                "body": body,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": repr(exc),
            }

    def _wait_response(self, rpc_id: int, timeout: int) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                msg = self._message_queue.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break

            if isinstance(msg, dict) and msg.get("id") == rpc_id:
                return msg

        return None

    def _parse_tool_response(
        self,
        response: Optional[Dict[str, Any]],
    ) -> Tuple[str, Any, bool, Optional[str]]:
        if response is None:
            return "", None, True, "no json-rpc response"

        if "error" in response:
            return "", None, True, json.dumps(response.get("error"), ensure_ascii=False)

        result = response.get("result") or {}
        is_error = bool(result.get("isError"))

        content = result.get("content")
        texts: List[str] = []

        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("text") is not None:
                    texts.append(str(item.get("text")))
                else:
                    texts.append(json.dumps(item, ensure_ascii=False))

        text = "\n".join(texts)
        if len(text) > self.max_text_length:
            text = text[: self.max_text_length] + "\n...[TRUNCATED]"

        content_json = None
        if text:
            try:
                content_json = json.loads(text)
            except Exception:
                content_json = None

        err = None
        if is_error:
            err = text[:2000] or "tool returned isError=true"

        return text, content_json, is_error, err
