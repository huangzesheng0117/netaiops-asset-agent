from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urljoin

import requests

from netaiops_asset.config_loader import get_config


class LLMClient:
    def __init__(self) -> None:
        cfg = get_config().get("llm", {})

        self.enabled = bool(cfg.get("enabled", False))
        self.provider = str(cfg.get("provider", "openai_compatible"))

        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        self.fallback_base_urls = [str(x).rstrip("/") for x in cfg.get("fallback_base_urls", []) if str(x).strip()]
        self.fallback_enabled = bool(cfg.get("fallback_enabled", False))
        self.long_timeout_base_url = str(cfg.get("long_timeout_base_url", "")).rstrip("/")

        self.chat_path = str(cfg.get("chat_completions_path", "/chat/completions"))
        self.models_path = str(cfg.get("models_path", "/models"))

        self.model = str(cfg.get("model", ""))
        self.api_key_env = str(cfg.get("api_key_env", "NETAIOPS_LLM_API_KEY"))
        self.api_key = os.getenv(self.api_key_env, "")

        self.timeout = int(cfg.get("timeout", 60) or 60)
        self.verify_ssl = bool(cfg.get("verify_ssl", False))

        self.temperature = cfg.get("temperature", 0)
        self.top_p = cfg.get("top_p", None)
        self.max_tokens = int(cfg.get("max_tokens", 900) or 900)
        self.max_tokens_param = str(cfg.get("max_tokens_param", "max_tokens"))

        self.stream = bool(cfg.get("stream", False))
        self.response_format = cfg.get("response_format", {"type": "json_object"})
        self.thinking = cfg.get("thinking", {"type": "disabled"})

        self.request_retries = int(cfg.get("request_retries", 1) or 1)
        self.retry_backoff_seconds = float(cfg.get("retry_backoff_seconds", 1.0) or 1.0)
        self.retry_status_codes = set(int(x) for x in cfg.get("retry_status_codes", [429, 500, 502, 503, 504]))

        self.channel_names = str(cfg.get("channel_names", "") or "").strip()

    def _join(self, base_url: str, path: str) -> str:
        return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))

    def chat_url(self, base_url: str | None = None) -> str:
        base = (base_url or self.base_url).rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return self._join(base, self.chat_path)

    def models_url(self, base_url: str | None = None) -> str:
        base = (base_url or self.base_url).rstrip("/")
        if base.endswith("/models"):
            return base
        return self._join(base, self.models_path)

    def masked_config(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "base_url": self.base_url,
            "chat_url": self.chat_url(),
            "models_url": self.models_url(),
            "fallback_base_urls": self.fallback_base_urls,
            "fallback_enabled": self.fallback_enabled,
            "long_timeout_base_url": self.long_timeout_base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "api_key_configured": bool(self.api_key),
            "timeout": self.timeout,
            "verify_ssl": self.verify_ssl,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "max_tokens_param": self.max_tokens_param,
            "stream": self.stream,
            "response_format": self.response_format,
            "thinking": self.thinking,
            "request_retries": self.request_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "retry_status_codes": sorted(list(self.retry_status_codes)),
            "channel_names": self.channel_names,
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.channel_names:
            params["channel_names"] = self.channel_names
        return params

    @staticmethod
    def _response_headers(resp: requests.Response) -> dict[str, Any]:
        return {
            "x_channel_name": resp.headers.get("x-channel-name") or resp.headers.get("X-Channel-Name"),
            "x_oneapi_request_id": resp.headers.get("x-oneapi-request-id") or resp.headers.get("X-Oneapi-Request-Id"),
            "x_request_id": resp.headers.get("x-request-id") or resp.headers.get("X-Request-Id"),
        }

    def _base_url_candidates(self) -> list[str]:
        candidates = [self.base_url]
        if self.fallback_enabled:
            candidates.extend(self.fallback_base_urls)
        result = []
        for item in candidates:
            if item and item not in result:
                result.append(item)
        return result

    def list_models(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "message": "LLM is disabled", "config": self.masked_config()}
        if not self.base_url:
            return {"status": "error", "error_code": "LLM_EMPTY_BASE_URL", "message": "llm.base_url is empty"}
        if not self.api_key:
            return {"status": "error", "error_code": "LLM_EMPTY_API_KEY", "message": f"LLM api key env {self.api_key_env} is empty"}

        start = time.time()
        try:
            resp = requests.get(
                self.models_url(),
                headers=self._headers(),
                params=self._params(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            latency_ms = int((time.time() - start) * 1000)

            try:
                data = resp.json()
            except Exception:
                return {
                    "status": "error",
                    "error_code": "LLM_MODELS_NON_JSON_RESPONSE",
                    "http_status": resp.status_code,
                    "latency_ms": latency_ms,
                    "message": "LLM models response is not JSON",
                    "text_preview": resp.text[:500],
                    "headers": self._response_headers(resp),
                    "config": self.masked_config(),
                }

            return {
                "status": "ok" if resp.status_code < 400 else "error",
                "error_code": None if resp.status_code < 400 else "LLM_MODELS_HTTP_ERROR",
                "http_status": resp.status_code,
                "latency_ms": latency_ms,
                "models_url": self.models_url(),
                "model_configured": self.model,
                "model_available": self._model_available(data),
                "data": data,
                "headers": self._response_headers(resp),
                "config": self.masked_config(),
            }

        except requests.RequestException as exc:
            return {
                "status": "error",
                "error_code": "LLM_MODELS_REQUEST_ERROR",
                "message": f"LLM models request failed: {type(exc).__name__}: {exc}",
                "config": self.masked_config(),
            }

    def _model_available(self, data: Any) -> bool:
        try:
            items = data.get("data", []) if isinstance(data, dict) else []
            for item in items:
                if isinstance(item, dict) and item.get("id") == self.model:
                    return True
            return False
        except Exception:
            return False

    def _build_payload(self, messages: list[dict[str, str]], overrides: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": overrides.get("model", self.model),
            "messages": messages,
            "stream": bool(overrides.get("stream", self.stream)),
        }

        temperature = overrides.get("temperature", self.temperature)
        if temperature is not None:
            payload["temperature"] = temperature

        top_p = overrides.get("top_p", self.top_p)
        if top_p is not None:
            payload["top_p"] = top_p

        max_tokens = int(overrides.get("max_tokens", self.max_tokens))
        payload[self.max_tokens_param] = max_tokens

        response_format = overrides.get("response_format", self.response_format)
        if response_format is not False and response_format is not None:
            payload["response_format"] = response_format

        thinking = overrides.get("thinking", self.thinking)
        if thinking is not False and thinking is not None:
            payload["thinking"] = thinking

        return payload

    def chat(self, messages: list[dict[str, str]], **overrides: Any) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "message": "LLM is disabled", "config": self.masked_config()}
        if not self.base_url:
            return {"status": "error", "error_code": "LLM_EMPTY_BASE_URL", "message": "llm.base_url is empty"}
        if not self.model:
            return {"status": "error", "error_code": "LLM_EMPTY_MODEL", "message": "llm.model is empty"}
        if not self.api_key:
            return {"status": "error", "error_code": "LLM_EMPTY_API_KEY", "message": f"LLM api key env {self.api_key_env} is empty"}

        payload = self._build_payload(messages, overrides)
        timeout = int(overrides.get("timeout", self.timeout))

        last_error: dict[str, Any] | None = None
        base_candidates = self._base_url_candidates()

        for base_idx, base_url in enumerate(base_candidates):
            attempts = max(1, self.request_retries + 1)

            for attempt in range(attempts):
                start = time.time()
                try:
                    resp = requests.post(
                        self.chat_url(base_url),
                        headers=self._headers(),
                        params=self._params(),
                        json=payload,
                        timeout=timeout,
                        verify=self.verify_ssl,
                    )
                    latency_ms = int((time.time() - start) * 1000)
                    headers = self._response_headers(resp)

                    try:
                        data = resp.json()
                    except Exception:
                        last_error = {
                            "status": "error",
                            "error_code": "LLM_NON_JSON_RESPONSE",
                            "http_status": resp.status_code,
                            "latency_ms": latency_ms,
                            "message": "LLM response is not JSON",
                            "text_preview": resp.text[:500],
                            "headers": headers,
                            "base_url_used": base_url,
                            "config": self.masked_config(),
                        }
                        break

                    if resp.status_code >= 400:
                        last_error = {
                            "status": "error",
                            "error_code": "LLM_HTTP_ERROR",
                            "http_status": resp.status_code,
                            "latency_ms": latency_ms,
                            "message": f"LLM HTTP error: {resp.status_code}",
                            "payload_preview": data if isinstance(data, dict) else str(data)[:500],
                            "headers": headers,
                            "base_url_used": base_url,
                            "config": self.masked_config(),
                        }

                        if resp.status_code in self.retry_status_codes and attempt < attempts - 1:
                            time.sleep(self.retry_backoff_seconds * (attempt + 1))
                            continue

                        break

                    requested_model = str(payload.get("model") or "")
                    max_tokens_used = payload.get(self.max_tokens_param)
                    reported_model = data.get("model") if isinstance(data, dict) else None
                    choices = data.get("choices") if isinstance(data, dict) else None

                    if not isinstance(choices, list) or not choices:
                        last_error = {
                            "status": "error",
                            "error_code": "LLM_EMPTY_CHOICES",
                            "http_status": resp.status_code,
                            "latency_ms": latency_ms,
                            "message": "LLM response choices is empty",
                            "headers": headers,
                            "base_url_used": base_url,
                            "requested_model": requested_model,
                            "reported_model": reported_model,
                            "finish_reason": None,
                            "max_tokens_used": max_tokens_used,
                            "content_length": 0,
                            "raw_choices_count": 0,
                            "config": self.masked_config(),
                        }
                        break

                    first_choice = choices[0] if isinstance(choices[0], dict) else {}
                    finish_reason = first_choice.get("finish_reason")
                    message = first_choice.get("message")

                    if not isinstance(message, dict):
                        last_error = {
                            "status": "error",
                            "error_code": "LLM_EMPTY_MESSAGE",
                            "http_status": resp.status_code,
                            "latency_ms": latency_ms,
                            "message": "LLM response message is missing",
                            "headers": headers,
                            "base_url_used": base_url,
                            "requested_model": requested_model,
                            "reported_model": reported_model,
                            "finish_reason": finish_reason,
                            "max_tokens_used": max_tokens_used,
                            "content_length": 0,
                            "raw_choices_count": len(choices),
                            "config": self.masked_config(),
                        }
                        break

                    raw_content = message.get("content")
                    content = raw_content.strip() if isinstance(raw_content, str) else ""

                    if not content:
                        last_error = {
                            "status": "error",
                            "error_code": "LLM_EMPTY_CONTENT",
                            "http_status": resp.status_code,
                            "latency_ms": latency_ms,
                            "message": "LLM response message.content is empty",
                            "headers": headers,
                            "base_url_used": base_url,
                            "requested_model": requested_model,
                            "reported_model": reported_model,
                            "finish_reason": finish_reason,
                            "max_tokens_used": max_tokens_used,
                            "content_length": 0,
                            "raw_choices_count": len(choices),
                            "config": self.masked_config(),
                        }
                        break

                    return {
                        "status": "ok",
                        "http_status": resp.status_code,
                        "latency_ms": latency_ms,
                        "model": requested_model,
                        "requested_model": requested_model,
                        "reported_model": reported_model,
                        "finish_reason": finish_reason,
                        "max_tokens_used": max_tokens_used,
                        "content": content,
                        "content_length": len(content),
                        "usage": data.get("usage") if isinstance(data, dict) else None,
                        "headers": headers,
                        "base_url_used": base_url,
                        "retry_attempts": attempt,
                        "fallback_index": base_idx,
                        "raw_choices_count": len(choices),
                    }

                except requests.Timeout as exc:
                    last_error = {
                        "status": "error",
                        "error_code": "LLM_TIMEOUT",
                        "message": f"LLM request timeout after {timeout}s",
                        "detail": str(exc),
                        "base_url_used": base_url,
                        "config": self.masked_config(),
                    }
                    if attempt < attempts - 1:
                        time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue

                except requests.RequestException as exc:
                    last_error = {
                        "status": "error",
                        "error_code": "LLM_REQUEST_ERROR",
                        "message": f"LLM request failed: {type(exc).__name__}: {exc}",
                        "base_url_used": base_url,
                        "config": self.masked_config(),
                    }
                    if attempt < attempts - 1:
                        time.sleep(self.retry_backoff_seconds * (attempt + 1))
                        continue

        return last_error or {"status": "error", "error_code": "LLM_UNKNOWN_ERROR", "message": "LLM request failed"}

    def probe(self) -> dict[str, Any]:
        result = self.chat(
            [
                {"role": "system", "content": "你是连通性测试助手。"},
                {"role": "user", "content": "请只回复 OK。"},
            ],
            max_tokens=max(1200, self.max_tokens),
            temperature=0,
            top_p=None,
            response_format=False,
            thinking={"type": "disabled"},
        )
        result["config"] = self.masked_config()
        if result.get("status") == "ok":
            result["content_preview"] = str(result.get("content", ""))[:120]
        return result
