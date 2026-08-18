from __future__ import annotations

"""Thin Engine-to-Worker Runtime adapter.

The Engine owns project/session scheduling.  This module only normalizes one task
attempt, builds the fixed Worker registry, adapts the active provider, and returns the
single Runtime implementation.
"""

import asyncio
import hashlib
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from knowe_adapters.model_adapter import ProviderAdapterConfig, ProviderModelAdapter

from .agent_runtime import AttemptProcessRegistry
from .config import CONFIG
from .runtime import (
    EventEmitter,
    RuntimeConfig,
    RuntimeStatus,
    TaskEnvelope,
    TaskRun,
    TaskState,
    WorkerRuntime,
)
from .tools_knowe import (
    build_worker_registry,
    close_worker_browser_session,
)
from . import tool_ledger


# [I-2] Removed: _PATH_TOKEN_RE / _MUTATION_RE / _DELETE_RE. Runtime no longer
# parses the natural-language goal to guess expected artifacts, mutation intent,
# or deletion intent. The goal is opaque payload handed to the LLM. Any structured
# expectation must arrive as explicit envelope metadata, never be inferred here.


class WorkerContextError(RuntimeError):
    """A required explicit context reference could not be loaded safely."""

    def __init__(self, code: str, reference: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "required_context_unavailable")
        self.reference = str(reference or "required_context")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reference": self.reference,
            "message": str(self),
            "repair_action": "Restore or replace the required project context, then retry.",
        }


def _unique_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, Iterable) or isinstance(value, (bytes, bytearray, Mapping)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _normalize_relative_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix()


def _normalize_task_metadata(envelope: TaskEnvelope) -> dict[str, Any]:
    """Normalize only the mechanical/safety fields Runtime actually needs.

    [I-2] No inference from the goal. No expected-artifact/deletion guessing, no
    artifact_required derivation. Runtime no longer validates completion (I-1), so
    those values had no consumer left. The only fields normalized here are:

    - ``authorized_external_roots`` — explicit safety allow-list, cleaned.

    [v1.0.23.5] ``delete_intent`` 安全门整体移除：不再规范化/透传该字段，
    删除权限对一切任务放行（路径安全校验保留在 safe_delete_file 工具层）。
    """

    metadata = dict(envelope.metadata or {})
    roots = _unique_strings(metadata.get("authorized_external_roots"))
    return {
        **metadata,
        "authorized_external_roots": list(roots),
    }


def _inject_user_address_prompt(runtime: WorkerRuntime, metadata: Mapping[str, Any]) -> WorkerRuntime:
    """Append the current user-address contract at the Worker's highest-attention edge.

    The value is transport context from TaskEnvelope metadata, never a Worker tool argument.
    Empty/unset settings add no prompt line.
    """

    line = str(metadata.get("user_address") or "").strip()
    if not line:
        return runtime
    runtime.prompt = f"{runtime.prompt.rstrip()}\n\n{line}"
    return runtime


class WorkerRuntimeFactory:
    """Build one fixed-tool Runtime for one Engine task attempt."""

    def __init__(self, config: Any = CONFIG) -> None:
        self.config = config

    @staticmethod
    def execution_context() -> AbstractContextManager[tool_ledger.ToolAudit]:
        """Return a clean Actor-local audit scope for one Worker attempt.

        The Runtime event relay is the sole user-visible Worker activity source.
        Legacy instrumented business tools may still record internal audit facts,
        but they deliberately receive no inherited Coordinator activity emitter.
        """

        return tool_ledger.actor_scope()

    @staticmethod
    def _workspace(engine: Any, envelope: TaskEnvelope) -> Path:
        raw = envelope.scope_root or getattr(engine, "workspace_root", "")
        if not raw:
            raise RuntimeError("Worker task has no project workspace")
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise RuntimeError(f"Worker workspace is unavailable: {root}")
        return root

    def prepare_envelope(self, envelope: TaskEnvelope) -> TaskEnvelope:
        metadata = _normalize_task_metadata(envelope)
        return replace(envelope, metadata=metadata)

    def prepare_context(self, engine: Any, envelope: TaskEnvelope) -> TaskEnvelope:
        """Prepare explicit references without turning size into permanent fact loss.

        Small UTF-8 project files may be carried inline in full.  Larger files are
        represented by a stable project-relative path, byte size, SHA-256 digest,
        required flag, and an instruction to page them with ``safe_read_file``.  Runtime
        never receives a silently cropped prefix.
        """

        root = self._workspace(engine, envelope)
        inline_bytes = max(0, int(getattr(
            self.config, "runtime_inline_reference_bytes", 32_768
        ) or 0))
        refs = []
        warnings: list[dict[str, str]] = []

        def digest_file(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        for reference in envelope.context_refs:
            metadata = dict(reference.metadata or {})
            required = bool(metadata.get("required", False))
            candidate = _normalize_relative_path(reference.ref)

            # A non-file reference may already carry authoritative inline content.  Keep
            # it verbatim: the Gateway has no alternate source from which to recover it.
            if not candidate:
                if reference.summary.strip() or str(metadata.get("content") or "").strip():
                    refs.append(reference)
                    continue
                if required:
                    raise WorkerContextError(
                        "required_context_unavailable",
                        reference.ref,
                        f"Required context is unavailable or unsafe: {reference.ref}",
                    )
                warnings.append({
                    "reference": reference.ref,
                    "message": "reference is not project-relative",
                })
                refs.append(reference)
                continue

            try:
                path = root / candidate
                if path.is_symlink():
                    raise ValueError("symlink references are not allowed")
                resolved = path.resolve(strict=True)
                if resolved != root and root not in resolved.parents:
                    raise ValueError("reference escapes project workspace")
                if not resolved.is_file() or resolved.is_symlink():
                    raise ValueError("reference is not a regular file")
                size = resolved.stat().st_size
                sha256 = digest_file(resolved)
                descriptor = {
                    **{k: v for k, v in metadata.items() if k != "content"},
                    "resolved_project_path": candidate,
                    "byte_size": size,
                    "sha256": sha256,
                    "required": required,
                    "content_mode": "inline" if size <= inline_bytes else "project_file_ref",
                }
                if size <= inline_bytes:
                    payload = resolved.read_bytes()
                    text = payload.decode("utf-8")
                    refs.append(replace(reference, summary=text, metadata=descriptor))
                else:
                    descriptor["read_instruction"] = (
                        "Use safe_read_file(path=<resolved_project_path>, start_line=..., "
                        "end_line=...) repeatedly until truncated=false to inspect the full source."
                    )
                    refs.append(replace(
                        reference,
                        summary=(
                            f"Project file reference {candidate!r}; {size} bytes; "
                            f"sha256={sha256}; full body is not inlined."
                        ),
                        metadata=descriptor,
                    ))
            except (OSError, UnicodeError, ValueError) as exc:
                if required:
                    raise WorkerContextError(
                        "required_context_unavailable",
                        reference.ref,
                        f"Required context is unavailable or unsafe: {reference.ref}",
                    ) from exc
                warnings.append({"reference": reference.ref, "message": str(exc)[:240]})
                refs.append(reference)

        metadata = dict(envelope.metadata)
        if warnings:
            metadata["context_warnings"] = warnings
        return replace(envelope, context_refs=tuple(refs), metadata=metadata)

    @staticmethod
    def blocked_run(engine: Any, envelope: TaskEnvelope, exc: BaseException) -> TaskRun:
        detail = exc.to_dict() if callable(getattr(exc, "to_dict", None)) else {
            "code": "worker_preflight_blocked",
            "reference": "worker_context",
            "message": str(exc),
            "repair_action": "Repair the task input and retry.",
        }
        run = TaskRun(
            envelope=envelope,
            state=TaskState.IDLE,
            version=1,
            final_candidate=str(detail.get("message") or str(exc)),
            terminal_reason=str(detail.get("code") or "worker_preflight_blocked"),
            dependency=str(detail.get("reference") or "worker_context"),
            metadata={
                "completion_status": RuntimeStatus.BLOCKED.value,
                "gaps": [str(detail.get("message") or str(exc))],
                "gap_details": [dict(detail)],
                "next_actions": [str(detail.get("repair_action") or "Repair the task input and retry.")],
            },
        )
        return run

    def create(
        self,
        engine: Any,
        worker_id: str,
        envelope: TaskEnvelope,
        agent: Any,
    ) -> WorkerRuntime:
        root = self._workspace(engine, envelope)
        metadata = _normalize_task_metadata(envelope)
        if metadata != envelope.metadata:
            envelope = replace(envelope, metadata=metadata)

        completion_store = getattr(engine, "completion_store", None)
        token_id = str(metadata.get("resume_wait_token_id") or "").strip()
        if completion_store is not None:
            if token_id:
                token = completion_store.get_wait_token(token_id)
                if token is None or str(token.status) != "resuming":
                    raise RuntimeError(f"invalid or inactive wait token: {token_id}")
                if (
                    token.worker_id != worker_id
                    or token.task_id != envelope.task_id
                    or token.attempt_id != envelope.attempt_id
                ):
                    raise RuntimeError("WAITING resume changed task/attempt/worker lineage")
            else:
                completion_store.assert_worker_available(
                    worker_id,
                    supersedes_task_id=str(metadata.get("supersedes_task_id") or ""),
                )

        model = ProviderModelAdapter.from_legacy(
            agent,
            config=ProviderAdapterConfig(
                stream=True,
                max_tokens=None,
            ),
        )

        cancellation_event = asyncio.Event()
        processes = AttemptProcessRegistry(
            project_id=envelope.project_id,
            task_id=envelope.task_id,
            attempt_id=envelope.attempt_id,
            workspace_root=root,
            max_processes=max(1, int(getattr(self.config, "process_max", 4))),
            log_bytes=max(4_096, int(getattr(self.config, "process_log_bytes", 64_000))),
        )
        registry = build_worker_registry(
            engine,
            worker_id,
            authorized_external_roots=metadata.get("authorized_external_roots") or (),
            cancellation_event=cancellation_event,
            process_registry=processes,
        )

        timeout = metadata.get(
            "timeout_seconds", getattr(self.config, "runtime_wall_clock_seconds", None)
        )
        timeout_seconds = None if timeout in (None, "", 0, "0") else float(timeout)
        # [I-4] Only the wall-clock stop is configurable. No turn ceiling, no tool
        # error cap, no correction budget — those are the LLM's decisions.
        # [v1.0.21.3] 语言化：prompts/<lang>/worker_prompt.md 存在则用之，否则回退 prompts/en/。
        from .prompt_resolver import resolve_prompt_path
        _resolved_prompt = resolve_prompt_path("worker_prompt.md")
        runtime_config = RuntimeConfig(
            timeout_seconds=timeout_seconds,
            prompt_path=str(_resolved_prompt) if _resolved_prompt else str(
                Path(__file__).parent / "prompts" / "en" / "worker_prompt.md"
            ),
        )
        def register_active_run(run: TaskRun) -> None:
            active = getattr(engine, "_worker_runtime_runs", None)
            if isinstance(active, dict):
                active[worker_id] = run

        # [v1.0.23.3] worker 推理增量直通 engine._fire（fire-and-forget）：
        #   与主 Agent 的 reasoning_delta_callback 同哲学——推理转发绝不阻塞
        #   provider 流。走 RuntimeEvent 全链路会因 await sink/listeners/WS
        #   广播拖死 worker 推理（实测「卡死」）。
        def _relay_reasoning(text: str) -> None:
            fire = getattr(engine, "_fire", None)
            if not callable(fire):
                return
            try:
                scope_id = engine._scope_for_task(envelope.task_id, envelope.attempt_id)
            except Exception:
                scope_id = ""
            fire({
                "type": "reasoning_delta",
                "agent_id": worker_id,
                "content": text,
                "task_id": envelope.task_id,
                "attempt_id": envelope.attempt_id,
                "scope_id": scope_id,
            })

        runtime = WorkerRuntime(
            model=model,
            registry=registry,
            config=runtime_config,
            workspace_root=root,
            cancellation_event=cancellation_event,
            process_registry=processes,
            cleanup_callbacks=(lambda: close_worker_browser_session(engine, worker_id),),
            events=EventEmitter(),
            on_run_started=register_active_run,
            reasoning_relay=_relay_reasoning,
        )
        # [v1.0.21.1.1 REQ-7] Worker 身份注入断链修复：engine 已把身份契约
        #   （名字/角色/id 绑死 + 专业块）写进 agent.ephemeral_system_prompt，
        #   但这里从未消费——Worker 的 system prompt 只有通用模板 + worklog 尾部，
        #   模型不知道「我是谁」，自我介绍时把 worklog 里的产品语境（"项目经理"）当身份。
        #   现在把身份块注入 prompt 最顶部：身份契约 > 通用纪律 > worklog 尾部。
        identity = getattr(agent, "ephemeral_system_prompt", "") or ""
        if identity:
            runtime.prompt = identity.rstrip() + "\n\n" + runtime.prompt
        return _inject_user_address_prompt(runtime, metadata)


DEFAULT_WORKER_RUNTIME_FACTORY = WorkerRuntimeFactory()


__all__ = [
    "DEFAULT_WORKER_RUNTIME_FACTORY",
    "WorkerContextError",
    "WorkerRuntimeFactory",
]
