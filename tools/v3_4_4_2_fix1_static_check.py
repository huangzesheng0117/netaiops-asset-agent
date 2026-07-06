#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def top_function(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return ast.get_source_segment(text, node) or ""
    raise RuntimeError(f"function not found: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--report-out", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    app_text = (root / "app.py").read_text(encoding="utf-8")
    bridge_text = (
        root / "netaiops_asset/chat_v3/followup_context.py"
    ).read_text(encoding="utf-8")
    doc_text = (
        root / "docs/v3_4_4_followup_convergence.md"
    ).read_text(encoding="utf-8")

    wrapper = top_function(
        app_text,
        "_v3_apply_chat_canary_takeover",
    )
    record_turn = top_function(
        bridge_text,
        "record_v3_turn",
    )
    normalize_turn = top_function(
        bridge_text,
        "_normalize_turn",
    )
    dedupe_turns = top_function(
        bridge_text,
        "_dedupe_turns",
    )

    forbidden = (
        "FOLLOWUP_KEYWORDS",
        "FOLLOWUP_HINTS",
        "classify_followup(",
        "is_followup_question(",
        "question_tokens",
        "followup_tokens",
    )
    forbidden_present = [
        token
        for token in forbidden
        if token in wrapper or token in bridge_text
    ]

    old_taken_only_block = (
        'store_result = record_v3_turn(\n'
        '                conversation_id=conversation_id,\n'
        '                user=user,\n'
        '                question=question,\n'
        '                response=mutated'
    )

    checks = {
        "wrapper_version_fix1": (
            '"version": "v3.4.4-2-fix1"' in wrapper
        ),
        "wrapper_has_central_return_observer": all(
            item in wrapper
            for item in (
                "context_record_enabled = False",
                "def _record_return_context(",
                "final_output = _record_return_context(",
                "context_record_attempted",
                "context_recorded",
                "context_record_source",
                "context_record_skip_reason",
            )
        ),
        "observer_enabled_only_after_canary_trigger": (
            wrapper.index("context_record_enabled = True")
            > wrapper.index("conversation_prefix_not_allowed")
        ),
        "observer_supports_v2_and_v3_sources": all(
            item in wrapper
            for item in (
                '"v2_or_non_taken_return"',
                '"v3_taken_return"',
            )
        ),
        "observer_uses_original_and_effective_ids": all(
            item in wrapper
            for item in (
                "conversation_id=conversation_id",
                "effective_conversation_id=(",
            )
        ),
        "observer_skips_invalid_returns": all(
            item in wrapper
            for item in (
                '"empty_answer"',
                '"error_status"',
                '"empty_question"',
                '"empty_original_conversation_id"',
            )
        ),
        "observer_exposes_store_error": (
            "v3_followup_context_store_error" in wrapper
        ),
        "taken_only_store_block_removed": (
            old_taken_only_block not in wrapper
        ),
        "record_turn_has_optional_observation_metadata": all(
            item in record_turn
            for item in (
                "effective_conversation_id: Optional[str] = None",
                'record_source: str = "v3_taken_return"',
                "turn_fingerprint",
                "deduplicated",
                "previous_turn_count",
            )
        ),
        "record_turn_requires_valid_answer": (
            'raise ValueError("answer or message is required")'
            in record_turn
        ),
        "record_turn_uses_original_conversation_id_path": (
            "path = _context_path(original_conversation_id)"
            in record_turn
        ),
        "normalization_preserves_dedupe_metadata": all(
            item in normalize_turn
            for item in (
                '"turn_fingerprint"',
                '"effective_conversation_id"',
                '"record_source"',
            )
        ),
        "dedupe_prefers_fingerprint": (
            'item.get("turn_fingerprint")' in dedupe_turns
        ),
        "bridge_does_not_decide_intent": (
            not forbidden_present
            and "IntentArbiter" not in bridge_text
            and "decide_intent" not in bridge_text
        ),
        "doc_describes_non_taken_persistence": all(
            item in doc_text
            for item in (
                "V2/non-taken",
                "return-path observation",
                "context_record_source",
                "v3.4.4-2-fix1",
            )
        ),
    }

    failures = [
        key for key, value in checks.items() if value is not True
    ]
    report = {
        "root": str(root),
        "checks": checks,
        "forbidden_present": forbidden_present,
        "failures": failures,
    }
    Path(args.report_out).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    if failures:
        raise SystemExit(
            "ERROR: V3.4-4-2 fix1 static check failed: "
            + ", ".join(failures)
        )

    print("v3_4_4_2_fix1_return_observer_static=OK")
    print("v3_4_4_2_fix1_original_conversation_id_static=OK")
    print("v3_4_4_2_fix1_dedupe_static=OK")
    print("v3_4_4_2_fix1_no_local_classifier_static=OK")
    print("result=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
