# [v1.0.13][R4] Durable Seen Speech ledger and deterministic anti-repeat helpers.
"""Persist exact text that has already been shown to a user.

The ledger is append-only JSONL.  Each visible id is idempotent, malformed tail rows are
ignored, and writes use one process-local lock plus fsync so a successful return means the
entry is available for the next Coordinator review turn.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class VisibleSpeech:
    project_id: str
    visible_id: str
    completion_id: str | None
    agent_id: str
    agent_name: str
    text: str
    seq: int
    shown_at: str
    audience: str

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        visible_id: str,
        completion_id: str | None,
        agent_id: str,
        agent_name: str,
        text: str,
        seq: int = 0,
        audience: str = "group",
    ) -> "VisibleSpeech":
        return cls(
            project_id=project_id,
            visible_id=visible_id,
            completion_id=completion_id or None,
            agent_id=agent_id,
            agent_name=agent_name,
            text=text,
            seq=max(0, int(seq or 0)),
            shown_at=datetime.now(timezone.utc).isoformat(),
            audience=audience or "group",
        )


class SeenSpeechLedger:
    """Small project-local append-only ledger with visible-id idempotency."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._loaded = False
        self._rows: dict[str, VisibleSpeech] = {}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.path.exists():
                return
            try:
                for raw in self.path.read_text(encoding="utf-8").splitlines():
                    try:
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            continue
                        row = VisibleSpeech(
                            project_id=str(value.get("project_id") or ""),
                            visible_id=str(value.get("visible_id") or ""),
                            completion_id=(str(value.get("completion_id")) if value.get("completion_id") else None),
                            agent_id=str(value.get("agent_id") or ""),
                            agent_name=str(value.get("agent_name") or ""),
                            text=str(value.get("text") or ""),
                            seq=max(0, int(value.get("seq") or 0)),
                            shown_at=str(value.get("shown_at") or ""),
                            audience=str(value.get("audience") or "group"),
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if row.visible_id and row.text:
                        self._rows[row.visible_id] = row
            except OSError:
                return

    def record(self, speech: VisibleSpeech) -> bool:
        """Append one exact visible event; return False for an idempotent duplicate."""

        if not speech.visible_id.strip() or not speech.text.strip():
            raise ValueError("visible_id and text are required")
        self._ensure_loaded()
        with self._lock:
            if speech.visible_id in self._rows:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(speech), ensure_ascii=False, sort_keys=True) + "\n"
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self._rows[speech.visible_id] = speech
            return True

    def by_completion(self, completion_id: str, *, limit: int = 3) -> list[VisibleSpeech]:
        self._ensure_loaded()
        wanted = completion_id.strip()
        if not wanted:
            return []
        rows = [row for row in self._rows.values() if row.completion_id == wanted]
        rows.sort(key=lambda row: (row.seq, row.shown_at, row.visible_id))
        return rows[-max(1, limit):]

    def recent(self, *, limit: int = 3) -> list[VisibleSpeech]:
        self._ensure_loaded()
        rows = list(self._rows.values())
        rows.sort(key=lambda row: (row.seq, row.shown_at, row.visible_id))
        return rows[-max(1, limit):]

    def count(self) -> int:
        """Return the authoritative number of visible rows in this ledger."""

        self._ensure_loaded()
        return len(self._rows)


def render_seen_speech_block(
    rows: Iterable[VisibleSpeech],
    *,
    total_count: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Render an advisory context block without editing visible speech.

    ``max_chars`` remains accepted for source compatibility but is deliberately not used
    to slice a sentence.  The selected rows are copied verbatim and point back to durable
    visible-event identifiers; the ledger, not this projection, is authoritative.
    """

    del max_chars
    selected = [row for row in rows if row.text]
    if not selected:
        return ""
    count = len(selected) if total_count is None else max(len(selected), int(total_count))
    from .i18n_backend import msg  # 局部导入：避免模块级语言固化
    lines = [
        msg("seen.001"),
        f"selected_rows={len(selected)}; authoritative_rows={count}",
        msg("seen.002"),
    ]
    for row in selected:
        # [v1.0.24.2] 账本字段（visible_id / completion_id）不进 LLM 上下文：
        # 本块职责=告知「用户已见正文、勿复述」，speaker+text 已足够；
        # 权威引用走 ledger（本投影仅 advisory，见函数 docstring）。
        lines.append(
            f"- speaker={row.agent_name or row.agent_id}\n  text={row.text}"
        )
    return "\n".join(lines)


def notification_from_unknown(value: Any) -> dict[str, Any] | None:
    """Strictly normalize the internal completion-review notification boundary."""

    if not isinstance(value, dict) or value.get("kind") != "completion_review":
        return None
    completion_id = str(value.get("completion_id") or "").strip()
    if not completion_id:
        return None
    allowed = {"accept", "rework", "pause", "complete", "terminate", "retry", "reject"}
    decisions = [
        str(item).strip().lower()
        for item in value.get("decision_required", [])
        if str(item).strip().lower() in allowed
    ] if isinstance(value.get("decision_required"), list) else []
    return {
        "kind": "completion_review",
        "completion_id": completion_id,
        "report_ref": str(value.get("report_ref") or "").strip(),
        "decision_required": decisions or ["accept", "rework"],
    }
