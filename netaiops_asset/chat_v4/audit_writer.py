# -*- coding: utf-8 -*-
"""Atomic local writer for V4 audit records.

The default path is not touched by importing this module. Callers and tests may
inject a temporary root.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Union

from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    ContextErrorKind,
    OperationStatus,
    V4AuditRecord,
)

DEFAULT_AUDIT_DIR = "/var/lib/netaiops-asset-agent/data/v4_audit"
DEFAULT_MAX_AUDIT_BYTES = 128 * 1024


@dataclass(frozen=True)
class AuditWriteResult:
    status: OperationStatus
    audit_ref: str = ""
    path: str = ""
    detail: str = ""
    error_kind: Optional[ContextErrorKind] = None

    @property
    def ok(self) -> bool:
        return self.status == OperationStatus.ok


class AuditWriter:
    def __init__(
        self,
        root: Optional[Union[str, Path]] = None,
        *,
        max_audit_bytes: int = DEFAULT_MAX_AUDIT_BYTES,
        file_mode: int = 0o640,
        dir_mode: int = 0o750,
    ) -> None:
        self.root = Path(
            root
            or os.getenv("NETAIOPS_V4_AUDIT_DIR", DEFAULT_AUDIT_DIR)
        )
        self.max_audit_bytes = max(4096, int(max_audit_bytes))
        self.file_mode = int(file_mode)
        self.dir_mode = int(dir_mode)

    @staticmethod
    def _normalize_audit_id(audit_id: str) -> str:
        value = str(audit_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
            raise ValueError("invalid audit_id")
        return value

    def path_for(self, audit_id: str) -> Path:
        return self.root / f"audit_{self._normalize_audit_id(audit_id)}.json"

    def _ensure_root(self) -> None:
        if self.root.exists():
            if not self.root.is_dir():
                raise OSError(f"not a directory: {self.root}")
            return
        self.root.mkdir(parents=True, mode=self.dir_mode, exist_ok=True)

    def _serialize(self, record: V4AuditRecord) -> bytes:
        data = ContextStore.sanitize_value(
            record.model_dump(mode="json")
        )
        payload = (
            json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > self.max_audit_bytes:
            raise ValueError("serialized audit exceeds max_audit_bytes")
        return payload

    def write(self, record: V4AuditRecord) -> AuditWriteResult:
        try:
            audit_id = self._normalize_audit_id(record.audit_id)
            payload = self._serialize(record)
            self._ensure_root()
            path = self.path_for(audit_id)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=str(self.root),
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
                dir_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            finally:
                if fd >= 0:
                    os.close(fd)
                if tmp_path.exists():
                    tmp_path.unlink()

            return AuditWriteResult(
                status=OperationStatus.ok,
                audit_ref=f"v4_audit:{audit_id}",
                path=str(path),
            )
        except ValueError as exc:
            return AuditWriteResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.invalid,
                detail=str(exc),
            )
        except PermissionError as exc:
            return AuditWriteResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.permission,
                detail=f"PermissionError: {exc}",
            )
        except OSError as exc:
            return AuditWriteResult(
                status=OperationStatus.error,
                error_kind=ContextErrorKind.write,
                detail=f"audit write error: {exc}",
            )
