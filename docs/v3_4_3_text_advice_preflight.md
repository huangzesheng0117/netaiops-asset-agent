# ChatBot V3.4-3 r6 Text and Advice Route Convergence

## Scope

V3.4-3 r6 replaces the old V3.3.17 local low-risk canary classifier with an Arbiter-driven wrapper for `general_chat` and `advice_analysis`.

## Fixes included

1. Preflight imports `app.py` after adding the project root to `sys.path`.
2. IntentAction enum values are normalized to plain strings.
3. Plan and decision are normalized before takeover gate and response generator.
4. The wrapper can rebuild Arbiter state when no local `v3_shadow_state` is available.
5. Existing `v3_shadow_state` and nested plan/decision-shaped objects are accepted only when their structured plan or decision contains a normalizable Arbiter action.
6. Invalid plan/decision-shaped candidates are audited and skipped; if no valid candidate remains, the wrapper calls `_v3_shadow_build()`.
7. V2 `answer` and `message` remain nested inside `v2_response`; they are not injected into the response generator's top-level context.
8. The direct wrapper regression test injects a polluted nested state with no action and requires rejection, rebuild, `generator_source=llm`, and `reason=taken`.
9. API smoke reads exact audit rows for the smoke conversations before deciding success or failure.

## Hard boundary

No local keyword, regex, or token table may decide the primary user intent. The following patterns are forbidden in the V3 route-return block:

```text
_v3_canary_low_risk_action
_v3_canary_contains_any
positive_danger_tokens
query_tokens
advice_tokens
general_tokens
explicit_advice_constraints
```
