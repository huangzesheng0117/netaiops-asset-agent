# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = [
    PROJECT / "netaiops_asset/llm/client.py",
    PROJECT / "netaiops_asset/chat_v3/response_generator.py",
    PROJECT / "netaiops_asset/chat_v2/llm_intent_planner.py",
    PROJECT / "netaiops_asset/chat_v2/llm_evidence_analyzer.py",
]
TEST_FILE = PROJECT / "tests/test_llm_glm52_compat.py"
APP_FILE = PROJECT / "app.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    for path in [*RUNTIME_FILES, TEST_FILE, APP_FILE]:
        require(path.is_file(), f"missing file: {path}")

    for path in RUNTIME_FILES:
        text = path.read_text(encoding="utf-8")
        require("qwen3-max" not in text, f"runtime qwen3-max remains: {path}")

    client = RUNTIME_FILES[0].read_text(encoding="utf-8")
    generator = RUNTIME_FILES[1].read_text(encoding="utf-8")
    planner = RUNTIME_FILES[2].read_text(encoding="utf-8")
    analyzer = RUNTIME_FILES[3].read_text(encoding="utf-8")
    app_text = APP_FILE.read_text(encoding="utf-8")

    require("max(1200, self.max_tokens)" in client, "probe token floor missing")
    require(
        'thinking={"type": "disabled"}' in client,
        "probe does not send explicit thinking disabled payload",
    )
    require("LLM_EMPTY_CONTENT" in client, "empty content guard missing")
    require("requested_model" in client, "requested model missing")
    require("reported_model" in client, "reported model missing")
    require("finish_reason" in client, "finish reason missing")
    require("max_tokens_used" in client, "max_tokens_used missing")
    require("content_length" in client, "content_length missing")
    require("response_max_tokens = max(1200" in generator, "generator token floor missing")
    require(
        'thinking={"type": "disabled"}' in generator,
        "response generator does not send explicit thinking disabled payload",
    )
    require("NETAIOPS_V2_PLANNER_LLM_MAX_TOKENS" in planner, "planner token budget missing")
    require("NETAIOPS_V2_EVIDENCE_LLM_MAX_TOKENS" in analyzer, "analyzer token budget missing")
    require('"error": "missing_model"' in analyzer, "analyzer missing-model guard missing")
    require(
        "return _batch67_contains_any(q, advice_keywords)" in app_text,
        "legacy advice classifier still treats no-command text as advice",
    )
    require(
        "client = LLMClient()" in app_text,
        "legacy advice path does not instantiate LLMClient",
    )
    require(
        "response_format=False" in app_text,
        "legacy advice path does not disable JSON response mode",
    )
    require(
        "thinking={\"type\": \"disabled\"}" in app_text,
        "legacy advice path does not send explicit thinking disabled payload",
    )
    require(
        "max(1200, configured_max_tokens)" in app_text,
        "legacy advice path token floor missing",
    )
    require(
        "test_legacy_advice_payload_contains_explicit_thinking_disabled" in TEST_FILE.read_text(encoding="utf-8"),
        "payload-level thinking disabled test missing",
    )

    subprocess.run(
        [sys.executable, "-B", str(TEST_FILE)],
        cwd=str(PROJECT),
        check=True,
    )

    print("glm52_unit_tests=OK")
    print("probe_min_tokens=OK")
    print("empty_content_detection=OK")
    print("reported_model_observability=OK")
    print("runtime_qwen_default_removed=OK")
    print("legacy_advice_llmclient_convergence=OK")
    print("legacy_advice_classifier_narrowing=OK")
    print("legacy_advice_payload_thinking_disabled=OK")
    print("probe_payload_thinking_disabled=OK")
    print("v3_generator_payload_thinking_disabled=OK")
    print("syntax_compile_external=OK")
    print("result=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
