# ChatBot V3.3-18 Closeout

## Scope

V3.3-18 is the closeout batch for ChatBot V3.3.

It does not expand production takeover scope. It validates and documents the guarded canary state achieved by V3.3-16 and V3.3-17.

## Confirmed baseline before closeout

- Project directory: `/opt/netaiops-asset-agent`
- Service: `netaiops-asset-agent.service`
- Port: `18081`
- Branch: `main`
- V3.3-17 commit is present in recent Git history
- Current pre-closeout HEAD: `98ba196`
- Current pre-closeout subject: `Fix V3.3 takeover audit observability`

## Current canary takeover boundary

The system remains in guarded canary mode.

```text
NETAIOPS_V3_TAKEOVER_ENABLED=1
NETAIOPS_V3_TAKEOVER_ALLOWED_USERS=v3_3_16_takeover,v3_3_17_takeover
NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX=v3-3-16-takeover-,v3-3-17-takeover-
NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS=general_chat,advice_analysis
NETAIOPS_V3_TAKEOVER_ALLOWED_SOURCES=llm
NETAIOPS_V3_TAKEOVER_AUDIT_DIR=/var/lib/netaiops-asset-agent/data/v3_takeover_audit
```

## Explicitly not enabled

The following are not enabled by V3.3 closeout:

- Full user takeover
- CMDB query takeover
- Device query takeover
- Command execution takeover
- Configuration change takeover
- Non-canary conversation takeover

## V3.3-18 closeout validations

The closeout tool validates:

1. Service is active.
2. Runtime V3 environment is loaded.
3. Systemd drop-in is present.
4. Audit directory is writable by `netaiops`.
5. `app.py` has exactly one V3 route-return helper marker pair.
6. Middleware `JSONResponse(...)` returns are wrapped.
7. `/api/v1/chat` route returns are wrapped.
8. `_v3_canary_write_audit()` exposes write errors instead of silently swallowing them.
9. V3.3-17 general text explanation canary takeover still works.
10. V3.3-17 advice-analysis canary takeover still works.
11. V3.3-16 compatibility canary still works.
12. Wrong user is blocked.
13. Wrong conversation prefix is blocked.
14. CMDB/device query is blocked.
15. Audit JSONL grows and contains taken/blocked records.

## Tag

This batch creates the annotated Git tag:

```text
chatbot-v3.3.18-closeout
```

Tag message:

```text
ChatBot V3.3 closeout: guarded canary takeover, audit observability, regression baseline
```

## Next step after V3.3

After this closeout, V3.4 should be treated as a new phase.

Recommended direction:

1. Decide whether to keep canary-only mode or introduce a wider controlled rollout.
2. Move route-return takeover helper from `app.py` into a dedicated module.
3. Expand action taxonomy only after adding integration-level smoke tests for each real route.
4. Keep audit and response error exposure as mandatory checks.
