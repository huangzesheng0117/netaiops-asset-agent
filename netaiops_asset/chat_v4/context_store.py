# -*- coding: utf-8 -*-
"""Canonical V4 context store.

The store is deterministic infrastructure only. It does not infer intent, call
external services, or execute commands.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Union

from pydantic import ValidationError

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.contracts import (
    MAX_ANSWER_CHARS,
    MAX_CONTEXT_ANALYSIS,
    MAX_CONTEXT_AUDIT_REFS,
    MAX_CONTEXT_EVIDENCE,
    MAX_CONTEXT_TURNS,
    MAX_QUESTION_CHARS,
    MAX_SUMMARY_CHARS,
    CanonicalContext,
    ContextErrorKind,
    ContextMigration,
    ContextOperationResult,
    ContextTurn,
    OperationStatus,
    utc_now,
)

DEFAULT_CONTEXT_DIR = "/var/lib/netaiops-asset-agent/data/v4_context"
DEFAULT_MAX_CONTEXT_BYTES = 512 * 1024
DEFAULT_MAX_GENERIC_STRING_CHARS = 12000
DEFAULT_MAX_RAW_OUTPUT_CHARS = 2000
DEFAULT_MAX_CONTAINER_ITEMS = 200
DEFAULT_MAX_SANITIZE_DEPTH = 12

_SENSITIVE_EXACT = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "cookie",
    "set_cookie",
    "bearer",
    "credential",
    "credentials",
    "private_key",
    "client_secret",
}
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_password",
    "_passwd",
    "_secret",
    "_token",
    "_cookie",
    "_authorization",
    "_credential",
    "_credentials",
    "_private_key",
)
_RAW_OUTPUT_EXACT = {
    "output",
    "output_preview",
    "text_preview",
    "raw_output",
    "full_output",
    "command_output",
    "stdout",
    "stderr",
    "device_output",
}
_RAW_OUTPUT_SUFFIXES = ("_raw_output", "_full_output", "_command_output")


class ContextStore:
    def __init__(
        self,
        root: Optional[Union[str, Path]] = None,
        *,
        max_context_bytes: int = DEFAULT_MAX_CONTEXT_BYTES,
        file_mode: int = 0o640,
        dir_mode: int = 0o750,
    ) -> None:
        self.root = Path(
            root
            or os.getenv("NETAIOPS_V4_CONTEXT_DIR", DEFAULT_CONTEXT_DIR)
        )
        self.max_context_bytes = max(4096, int(max_context_bytes))
        self.file_mode = int(file_mode)
        self.dir_mode = int(dir_mode)
        self.context_dir = self.root / "contexts"
        self.lock_dir = self.root / "locks"
        self.quarantine_dir = self.root / "quarantine"

    @staticmethod
    def _normalize_conversation_id(conversation_id: str) -> str:
        value = str(conversation_id or "").strip()
        if not value or len(value) > 256 or "\x00" in value:
            raise ValueError("invalid conversation_id")
        return value

    @classmethod
    def _digest(cls, conversation_id: str) -> str:
        normalized = cls._normalize_conversation_id(conversation_id)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def path_for(self, conversation_id: str) -> Path:
        return self.context_dir / f"context_{self._digest(conversation_id)}.json"

    def lock_path_for(self, conversation_id: str) -> Path:
        return self.lock_dir / f"context_{self._digest(conversation_id)}.lock"

    def _ensure_dir(self, path: Path) -> None:
        if path.exists():
            if not path.is_dir():
                raise OSError(f"not a directory: {path}")
            return
        path.mkdir(parents=True, mode=self.dir_mode, exist_ok=True)

    def _ensure_dirs(self) -> None:
        self._ensure_dir(self.root)
        self._ensure_dir(self.context_dir)
        self._ensure_dir(self.lock_dir)
        self._ensure_dir(self.quarantine_dir)

    @contextmanager
    def _lock(self, conversation_id: str) -> Iterator[None]:
        self._ensure_dirs()
        lock_path = self.lock_path_for(conversation_id)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, self.file_mode)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _normalized_key(key: Any) -> str:
        return str(key or "").strip().lower().replace("-", "_")

    @classmethod
    def _is_sensitive_key(cls, key: Any) -> bool:
        normalized = cls._normalized_key(key)
        return normalized in _SENSITIVE_EXACT or normalized.endswith(
            _SENSITIVE_SUFFIXES
        )

    @classmethod
    def _is_raw_output_key(cls, key: Any) -> bool:
        normalized = cls._normalized_key(key)
        return normalized in _RAW_OUTPUT_EXACT or normalized.endswith(
            _RAW_OUTPUT_SUFFIXES
        )

    @staticmethod
    def _truncate(value: Any, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        suffix = "...<truncated>"
        return text[: max(0, limit - len(suffix))] + suffix

    @classmethod
    def sanitize_value(
        cls,
        value: Any,
        *,
        key: Any = "",
        depth: int = 0,
    ) -> Any:
        if cls._is_sensitive_key(key):
            return "[REDACTED]"
        if depth >= DEFAULT_MAX_SANITIZE_DEPTH:
            return "<max-depth>"
        if isinstance(value, dict):
            result: Dict[str, Any] = {}
            for index, (raw_key, raw_value) in enumerate(value.items()):
                if index >= DEFAULT_MAX_CONTAINER_ITEMS:
                    result["__truncated_items__"] = True
                    break
                key_text = str(raw_key)
                result[key_text] = cls.sanitize_value(
                    raw_value,
                    key=key_text,
                    depth=depth + 1,
                )
            return result
        if isinstance(value, (list, tuple)):
            return [
                cls.sanitize_value(item, depth=depth + 1)
                for item in list(value)[:DEFAULT_MAX_CONTAINER_ITEMS]
            ]
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, str):
            limit = (
                DEFAULT_MAX_RAW_OUTPUT_CHARS
                if cls._is_raw_output_key(key)
                else DEFAULT_MAX_GENERIC_STRING_CHARS
            )
            return cls._truncate(value, limit)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return cls._truncate(value, DEFAULT_MAX_GENERIC_STRING_CHARS)

    @classmethod
    def _prepare_context(
        cls,
        value: Union[CanonicalContext, Dict[str, Any]],
    ) -> CanonicalContext:
        raw = (
            value.model_dump(mode="python")
            if isinstance(value, CanonicalContext)
            else deepcopy(dict(value))
        )
        raw = cls.sanitize_value(raw)

        raw["title"] = cls._truncate(raw.get("title"), 160)
        raw["topic"] = cls._truncate(raw.get("topic"), 512)
        raw["rolling_summary"] = cls._truncate(
            raw.get("rolling_summary"),
            MAX_SUMMARY_CHARS,
        )

        prepared_turns = []
        for turn in list(raw.get("recent_turns") or [])[-MAX_CONTEXT_TURNS:]:
            item = dict(turn) if isinstance(turn, dict) else {}
            item["question"] = cls._truncate(
                item.get("question"),
                MAX_QUESTION_CHARS,
            )
            item["answer_summary"] = cls._truncate(
                item.get("answer_summary"),
                MAX_ANSWER_CHARS,
            )
            prepared_turns.append(item)
        raw["recent_turns"] = prepared_turns
        raw["execution_evidence"] = list(
            raw.get("execution_evidence") or []
        )[-MAX_CONTEXT_EVIDENCE:]
        raw["analysis_history"] = list(
            raw.get("analysis_history") or []
        )[-MAX_CONTEXT_ANALYSIS:]
        raw["audit_refs"] = list(
            raw.get("audit_refs") or []
        )[-MAX_CONTEXT_AUDIT_REFS:]

        return CanonicalContext.model_validate(raw)

    def _read_bytes(self, path: Path) -> bytes:
        data = path.read_bytes()
        if len(data) > self.max_context_bytes:
            raise ValueError("context file exceeds max_context_bytes")
        return data

    def _quarantine(self, path: Path, reason: str) -> Path:
        self._ensure_dir(self.quarantine_dir)
        safe_reason = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "_"
            for ch in str(reason or "corrupt")
        )[:40]
        target = self.quarantine_dir / (
            f"{path.name}.{utc_now().replace(':', '').replace('+', '_')}"
            f".{os.getpid()}.{safe_reason}.corrupt"
        )
        os.replace(path, target)
        return target

    def _load_unlocked(
        self,
        conversation_id: str,
        *,
        quarantine_invalid: bool,
    ) -> ContextOperationResult:
        path = self.path_for(conversation_id)
        if not path.exists():
            return ContextOperationResult(
                status=OperationStatus.not_found,
                path=str(path),
                detail="context not found",
            )
        try:
            raw_bytes = self._read_bytes(path)
            data = json.loads(raw_bytes.decode("utf-8"))
        except PermissionError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.permission,
                path=str(path),
                detail=f"PermissionError: {exc}",
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            quarantine_path = ""
            if quarantine_invalid and path.exists():
                try:
                    quarantine_path = str(self._quarantine(path, "corrupt"))
                except PermissionError as quarantine_exc:
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.permission,
                        path=str(path),
                        detail=(
                            f"context corrupt; quarantine permission error: "
                            f"{quarantine_exc}"
                        ),
                    )
                except OSError as quarantine_exc:
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.write,
                        path=str(path),
                        detail=(
                            f"context corrupt; quarantine write error: "
                            f"{quarantine_exc}"
                        ),
                    )
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.corrupt,
                path=str(path),
                quarantine_path=quarantine_path,
                detail=f"context decode error: {exc}",
            )
        except OSError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.write,
                path=str(path),
                detail=f"context read error: {exc}",
            )

        try:
            context = CanonicalContext.model_validate(data)
        except ValidationError as exc:
            quarantine_path = ""
            if quarantine_invalid and path.exists():
                try:
                    quarantine_path = str(self._quarantine(path, "schema"))
                except PermissionError as quarantine_exc:
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.permission,
                        path=str(path),
                        detail=(
                            f"context schema invalid; quarantine permission "
                            f"error: {quarantine_exc}"
                        ),
                    )
                except OSError as quarantine_exc:
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.write,
                        path=str(path),
                        detail=(
                            f"context schema invalid; quarantine write error: "
                            f"{quarantine_exc}"
                        ),
                    )
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.schema,
                path=str(path),
                quarantine_path=quarantine_path,
                detail=f"context schema error: {exc}",
            )

        if context.conversation_id != self._normalize_conversation_id(
            conversation_id
        ):
            quarantine_path = ""
            if quarantine_invalid and path.exists():
                try:
                    quarantine_path = str(
                        self._quarantine(path, "conversation_id_mismatch")
                    )
                except PermissionError as quarantine_exc:
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.permission,
                        path=str(path),
                        detail=(
                            f"conversation_id mismatch; quarantine permission "
                            f"error: {quarantine_exc}"
                        ),
                    )
                except OSError as quarantine_exc:
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.write,
                        path=str(path),
                        detail=(
                            f"conversation_id mismatch; quarantine write "
                            f"error: {quarantine_exc}"
                        ),
                    )
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.schema,
                path=str(path),
                quarantine_path=quarantine_path,
                detail="stored conversation_id does not match requested id",
            )

        return ContextOperationResult(
            status=OperationStatus.ok,
            context=context,
            path=str(path),
        )

    def load(
        self,
        conversation_id: str,
        *,
        quarantine_invalid: bool = True,
    ) -> ContextOperationResult:
        try:
            normalized = self._normalize_conversation_id(conversation_id)
            with self._lock(normalized):
                return self._load_unlocked(
                    normalized,
                    quarantine_invalid=quarantine_invalid,
                )
        except ValueError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.invalid,
                detail=str(exc),
            )
        except PermissionError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.permission,
                detail=f"PermissionError: {exc}",
            )
        except OSError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.write,
                detail=f"context lock/read error: {exc}",
            )

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        if len(payload) > self.max_context_bytes:
            raise ValueError("serialized context exceeds max_context_bytes")
        self._ensure_dir(path.parent)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            os.fchmod(fd, self.file_mode)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                fd = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            if tmp_path.exists():
                tmp_path.unlink()

    def _serialize(self, context: CanonicalContext) -> bytes:
        return (
            json.dumps(
                context.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def save(
        self,
        context: Union[CanonicalContext, Dict[str, Any]],
        *,
        expected_revision: Optional[int] = None,
    ) -> ContextOperationResult:
        try:
            prepared = self._prepare_context(context)
            conversation_id = prepared.conversation_id
            with self._lock(conversation_id):
                current = self._load_unlocked(
                    conversation_id,
                    quarantine_invalid=True,
                )
                if current.status == OperationStatus.error:
                    return current
                current_revision = (
                    current.context.revision
                    if current.context is not None
                    else 0
                )
                if (
                    expected_revision is not None
                    and current_revision != expected_revision
                ):
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.conflict,
                        path=str(self.path_for(conversation_id)),
                        detail=(
                            f"revision conflict: expected={expected_revision}, "
                            f"actual={current_revision}"
                        ),
                    )

                now = utc_now()
                if current.context is not None:
                    prepared.created_at = current.context.created_at
                prepared.updated_at = now
                prepared.revision = current_revision + 1
                prepared = self._prepare_context(prepared)
                payload = self._serialize(prepared)
                self._atomic_write(self.path_for(conversation_id), payload)
                return ContextOperationResult(
                    status=OperationStatus.ok,
                    context=prepared,
                    path=str(self.path_for(conversation_id)),
                )
        except ValidationError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.schema,
                detail=f"context validation error: {exc}",
            )
        except ValueError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.invalid,
                detail=str(exc),
            )
        except PermissionError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.permission,
                detail=f"PermissionError: {exc}",
            )
        except OSError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.write,
                detail=f"context write error: {exc}",
            )

    def update(
        self,
        conversation_id: str,
        mutator: Callable[[CanonicalContext], Union[CanonicalContext, Dict[str, Any], None]],
        *,
        request_user_field: str = "",
        create: bool = True,
    ) -> ContextOperationResult:
        try:
            normalized = self._normalize_conversation_id(conversation_id)
            with self._lock(normalized):
                current = self._load_unlocked(
                    normalized,
                    quarantine_invalid=True,
                )
                if current.status == OperationStatus.error:
                    return current
                if current.status == OperationStatus.not_found:
                    if not create:
                        return current
                    base = CanonicalContext(
                        conversation_id=normalized,
                        request_user_field=request_user_field,
                    )
                    current_revision = 0
                else:
                    assert current.context is not None
                    base = current.context.model_copy(deep=True)
                    current_revision = base.revision

                working = base.model_copy(deep=True)
                mutated = mutator(working)
                candidate = working if mutated is None else mutated
                prepared = self._prepare_context(candidate)
                if prepared.conversation_id != normalized:
                    return ContextOperationResult(
                        status=OperationStatus.error,
                        error_kind=ContextErrorKind.schema,
                        path=str(self.path_for(normalized)),
                        detail="mutator cannot change conversation_id",
                    )
                now = utc_now()
                prepared.created_at = base.created_at
                prepared.updated_at = now
                prepared.revision = current_revision + 1
                prepared = self._prepare_context(prepared)
                self._atomic_write(
                    self.path_for(normalized),
                    self._serialize(prepared),
                )
                return ContextOperationResult(
                    status=OperationStatus.ok,
                    context=prepared,
                    path=str(self.path_for(normalized)),
                )
        except ValidationError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.schema,
                detail=f"context validation error: {exc}",
            )
        except ValueError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.invalid,
                detail=str(exc),
            )
        except PermissionError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.permission,
                detail=f"PermissionError: {exc}",
            )
        except OSError as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.write,
                detail=f"context update error: {exc}",
            )
        except Exception as exc:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.write,
                detail=f"context mutator error: {type(exc).__name__}: {exc}",
            )

    def append_turn(
        self,
        conversation_id: str,
        *,
        question: str,
        answer_summary: str,
        action: Optional[IntentAction] = None,
        planner_source: str = "",
        route_label: str = "",
        effective_conversation_id: str = "",
        record_source: str = "v4_response",
        request_user_field: str = "",
        topic: str = "",
        device_context: Optional[Dict[str, Any]] = None,
        last_intent: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ContextOperationResult:
        turn = ContextTurn(
            question=self._truncate(question, MAX_QUESTION_CHARS),
            answer_summary=self._truncate(
                answer_summary,
                MAX_ANSWER_CHARS,
            ),
            action=action,
            planner_source=planner_source,
            route_label=route_label,
            effective_conversation_id=(
                effective_conversation_id or conversation_id
            ),
            record_source=record_source,
            metadata=self.sanitize_value(metadata or {}),
        )
        state = {"deduplicated": False}

        def mutator(context: CanonicalContext) -> CanonicalContext:
            fingerprints = {
                item.turn_fingerprint for item in context.recent_turns
            }
            if turn.turn_fingerprint in fingerprints:
                state["deduplicated"] = True
            else:
                context.recent_turns.append(turn)
            context.recent_turns = context.recent_turns[-MAX_CONTEXT_TURNS:]
            if topic:
                context.topic = self._truncate(topic, 512)
            if device_context:
                context.device_context = self.sanitize_value(device_context)
            if last_intent:
                context.last_intent = self.sanitize_value(last_intent)
            return context

        result = self.update(
            conversation_id,
            mutator,
            request_user_field=request_user_field,
            create=True,
        )
        if result.status == OperationStatus.ok:
            result.deduplicated = state["deduplicated"]
            result.metadata["turn_fingerprint"] = turn.turn_fingerprint
        return result

    def add_audit_ref(
        self,
        conversation_id: str,
        audit_ref: str,
        *,
        request_user_field: str = "",
    ) -> ContextOperationResult:
        normalized_ref = self._truncate(audit_ref, 1024).strip()
        if not normalized_ref:
            return ContextOperationResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.invalid,
                detail="audit_ref is required",
            )
        state = {"deduplicated": False}

        def mutator(context: CanonicalContext) -> CanonicalContext:
            if normalized_ref in context.audit_refs:
                state["deduplicated"] = True
            else:
                context.audit_refs.append(normalized_ref)
            context.audit_refs = context.audit_refs[-MAX_CONTEXT_AUDIT_REFS:]
            return context

        result = self.update(
            conversation_id,
            mutator,
            request_user_field=request_user_field,
            create=True,
        )
        if result.status == OperationStatus.ok:
            result.deduplicated = state["deduplicated"]
        return result

    def new_context(
        self,
        conversation_id: str,
        *,
        request_user_field: str = "",
        title: str = "",
        migration: Optional[ContextMigration] = None,
    ) -> CanonicalContext:
        return CanonicalContext(
            conversation_id=self._normalize_conversation_id(conversation_id),
            request_user_field=request_user_field,
            title=self._truncate(title, 160),
            migration=migration or ContextMigration(),
        )
