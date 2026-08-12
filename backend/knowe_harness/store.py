from __future__ import annotations

"""Immutable local storage for the shared ``TaskEnvelope`` model."""

import json
import os
from pathlib import Path
from typing import Any, Mapping

from backend.runtime import TaskEnvelope


class TaskEnvelopeStore:
    def __init__(self, internal_workspace: str | Path) -> None:
        root = Path(internal_workspace).expanduser().resolve()
        self.root = root / "runtime" / "task-envelopes"
        self.root.mkdir(parents=True, exist_ok=True)

    def envelope_path(self, task_id: str, attempt_id: str) -> Path:
        return self.root / str(task_id) / f"{attempt_id}.json"

    @staticmethod
    def _create_json_once(path: Path, value: Mapping[str, Any]) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return True

    def commit(self, envelope: TaskEnvelope) -> tuple[TaskEnvelope, str]:
        path = self.envelope_path(envelope.task_id, envelope.attempt_id)
        ref = path.relative_to(self.root.parent.parent).as_posix()
        if self._create_json_once(path, envelope.to_dict()):
            return envelope, ref
        try:
            existing = TaskEnvelope.from_dict(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"stored TaskEnvelope is unreadable for {envelope.task_id}/{envelope.attempt_id}"
            ) from exc
        if existing.digest == envelope.digest:
            return existing, ref
        raise RuntimeError(
            f"immutable TaskEnvelope collision for {envelope.task_id}/{envelope.attempt_id}"
        )

    def get(self, task_id_or_ref: str, attempt_id: str = "") -> TaskEnvelope | None:
        raw = str(task_id_or_ref or "").strip().replace("\\", "/")
        if not raw:
            return None
        if raw.startswith("runtime/task-envelopes/") and raw.endswith(".json"):
            relative = raw.removeprefix("runtime/task-envelopes/")
            path = self.root / relative
        elif attempt_id:
            path = self.envelope_path(raw, attempt_id)
        else:
            task_dir = self.root / raw
            candidates = sorted(task_dir.glob("*.json"))
            path = candidates[-1] if candidates else Path("")
        if not path or not path.is_file():
            return None
        try:
            return TaskEnvelope.from_dict(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None


__all__ = ["TaskEnvelopeStore"]
