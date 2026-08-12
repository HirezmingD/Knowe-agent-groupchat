# knowe v0.9a — Harness B 批
"""
handoff.py — 交接目录体系（B-1）+ 注入模板（B-2）。

**这个文件里没有智能。** 它只做三件机械的事：
  1. 按规范拼文件名和目录（`handoffs/03-后端/report-03-fe_1-用户认证.md`）
  2. 按模板拼 Markdown（YAML frontmatter + 固定几段）
  3. 从文本里抠出标记（`NEXT_HANDOFF_DIR: handoffs/04-测试/`）

它不理解 Agent 干完了什么，也不判断报告写得好不好——那是 LLM 的事。
Harness 的活是：**把注意力钉在正确的路径上，然后把文件送到该看的人面前。**

为什么单开一个文件：engine.py 已经 800 行了，而这一批的东西（目录、序号、模板、正则）
是**纯数据处理**——不碰 asyncio、不碰 hub、不碰 gate。
单独放，才测得动（本批的自测就是直接跑它）。

═══ 目录长这样 ═══

    {workspace_root}/handoffs/
    ├── 01-需求分析/
    │   ├── instruction-01-fe_1-用户故事.md   ← 项目经理派活（审批通过那一刻落盘）
    │   ├── report-01-fe_1-用户故事.md        ← Worker 交差
    │   └── .approval-01.md                   ← Harness 自动写，双向链接
    └── 02-前端开发/
        └── …

序号（step）是**一步**的编号：一条 instruction、一份 report、一条 approval 共用一个。
阶段（phase）是目录的编号。两者各走各的，互不相干。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .i18n_backend import msg  # [v1.0.21.3] 指令/报告/审批模板按语言渲染
from typing import Any, Mapping

from knowe_provenance import normalize_provenance, unknown_legacy_provenance

__all__ = [
    "HandoffBook",
    "NEXT_DIR_MARKER",
    "keyword_of",
    "parse_next_dir",
    "REPORT_FORMAT_HINT",
]

#: 项目经理在回复里写这个标记 → Harness 把当前阶段目录切过去（B-2 ③）
NEXT_DIR_MARKER = "NEXT_HANDOFF_DIR"

#: `NEXT_HANDOFF_DIR: handoffs/04-测试/` —— 前面的 handoffs/ 和末尾的 / 都可有可无
_NEXT_DIR_RE = re.compile(
    rf"{NEXT_DIR_MARKER}\s*[:：]\s*`?(?:handoffs[/\\])?([^\s`\n]+?)[/\\]?`?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

#: 阶段目录名：`03-后端开发`
_PHASE_RE = re.compile(r"^(\d{2,})-(.+)$")

#: 交接文件名里的序号：`report-03-fe_1-用户认证.md` / `.approval-03.md`
_STEP_RE = re.compile(r"^\.?(?:report|instruction|approval)-(\d{2,})(?:-|\.)")

#: 文件名里不能出现的字符（Windows 尤其挑剔）
_BAD_NAME = re.compile(r"[^\w\u4e00-\u9fff-]+")

#: 关键词最多留这么长——它是给人认路的，不是摘要
KEYWORD_MAX = 12

DEFAULT_PHASE = "起步"


def keyword_of(text: str, fallback: str | None = None) -> str:
    """
    从一句话里抠一个**关键词**当文件名的一截。

    机械做法，不动脑子：取第一行 → 去掉标点空白 → 截前 12 个字。
    抠不出来就用 fallback。
    ★ 绝不能让文件名里出现 `/` `:` 这种东西 —— 那不是难看，那是写不进磁盘。
    """
    if not isinstance(text, str):
        return fallback or msg("hd.042")
    first = text.strip().splitlines()[0] if text.strip() else ""
    cleaned = _BAD_NAME.sub("", first)
    cleaned = cleaned.strip("-_")
    return cleaned[:KEYWORD_MAX] or fallback or msg("hd.042")


def parse_next_dir(text: str) -> str | None:
    """
    从项目经理的回复里抠出 `NEXT_HANDOFF_DIR: handoffs/04-测试/`。

    返回 `04-测试`（或 `测试` —— 没写序号也认，Harness 会自己补号）。
    没有标记 → None。
    """
    if not isinstance(text, str) or NEXT_DIR_MARKER.lower() not in text.lower():
        return None
    m = _NEXT_DIR_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    return raw or None


def strip_next_dir(text: str) -> str:
    """
    把标记那一行从**给用户看的话**里删掉。

    标记是写给 Harness 的暗号，不是说给人听的。
    让它出现在聊天气泡里，就像把舞台提示词念出声。
    """
    if not isinstance(text, str):
        return text
    lines = [ln for ln in text.splitlines()
             if NEXT_DIR_MARKER.lower() not in ln.lower()]
    return "\n".join(lines).strip()


# ═══════════════════════════════════════════════════════════════
# 模板（B-1）
# ═══════════════════════════════════════════════════════════════

#: Runtime Delivery → Handoff Report 的确定性格式提示。**必须和 write_report 输出一致**。
#: 第六段「知识引用」及接力字段由 Engine 从 DeliveryRecord、Task Journal
#: 组装，不依赖模型工具参数。
REPORT_FORMAT_HINT = """\
---
from: {你的 agent_id}
status: completed | partial | blocked | failed
created: YYYY-MM-DD
---
# Report：{任务关键词}

## 一、我完成了什么
## 二、是否完全符合上次指令
## 三、产出文件清单
## 四、需要注意的问题
## 五、自检
## 六、知识引用
"""

_REPORT_SECTIONS = (
    ("completed_what", "hd.001"),
    ("matches_instruction", "hd.002"),
    ("artifacts_md", "hd.003"),
    ("issues", "hd.004"),
    ("self_check", "hd.005"),
)

_INSTRUCTION_SECTIONS = (
    ("background", "hd.006"),
    ("previous", "hd.007"),
    ("task", "hd.008"),
    ("inputs", "hd.009"),
    ("acceptance", "hd.010"),
    ("notes", "hd.011"),
)

_LEGACY_STATUSES = ("completed", "partial", "blocked", "failed")
_COMPLETION_STATUSES = (
    "SUCCEEDED", "PARTIAL", "FAILED", "BLOCKED", "WAITING",
    "CANCELLED", "ROLLED_BACK", "SUPERSEDED",
)
_STATUSES = _LEGACY_STATUSES + _COMPLETION_STATUSES


def _today() -> str:
    return date.today().isoformat()


def _yaml(front: dict[str, str]) -> str:
    """极简 frontmatter。值里有冒号/换行就加引号——别指望 LLM 会替你转义。"""
    lines = ["---"]
    for k, v in front.items():
        val = str(v).replace("\n", " ").strip()
        if any(c in val for c in ':#"\'') or not val:
            val = '"' + val.replace('"', "'") + '"'
        lines.append(f"{k}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _sections(spec: tuple[tuple[str, str], ...], data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key, title in spec:
        body = str(data.get(key) or "").strip() or msg("hd.012")
        out += [f"## {msg(title)}", "", body, ""]
    return out


# ═══════════════════════════════════════════════════════════════
# 账本
# ═══════════════════════════════════════════════════════════════

class HandoffBook:
    """
    一个项目的交接账本。**状态全从磁盘上长出来**，不额外落一份盘。

    为什么不另存一个 json：目录本身就是账本。序号、阶段、谁交过报告——
    `ls handoffs/` 全看得见。再存一份，就多一个会跟磁盘对不上的东西
    （v0.8e 的教训：判据挂在会变的东西上，等于没有判据；而磁盘上的文件名
      是这里唯一不会撒谎的东西）。
    """

    def __init__(self, root: Path) -> None:
        #: {workspace_root}/handoffs
        self.root = Path(root)

    #: [v1.0.24.3] INT 审计目录：{internal_workspace}/audit（与 handoffs/ 平级，树外不可达）
    @property
    def audit_dir(self) -> Path:
        return self.root.parent / "audit"

    #: [v1.0.24.3] 全部 INT 审计报告（audit/ 树，递归）。
    #:   INT 仅供软件内部溯源/审计，任何面向用户/LLM 的扫描（handoffs/ 树）都扫不到它。
    def audit_reports(self) -> list[Path]:
        if not self.audit_dir.is_dir():
            return []
        return sorted(self.audit_dir.rglob("report-INT-*.md"))

    # ── 目录 / 序号 ──

    def _phases(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        out = [p for p in self.root.iterdir()
               if p.is_dir() and _PHASE_RE.match(p.name)]
        return sorted(out, key=lambda p: p.name)

    def current_phase(self) -> Path:
        """当前阶段目录。一个都没有 → 现开一个 `01-起步`。"""
        phases = self._phases()
        if phases:
            return phases[-1]
        return self.new_phase(DEFAULT_PHASE)

    def new_phase(self, name: str) -> Path:
        """
        开一个新阶段目录。名字里带不带序号都行：
          `测试` → `03-测试`；`04-测试` → 序号听项目经理的（但不许倒退）。
        已经存在的同名阶段 → 直接返回它，不重复建。
        """
        raw = (name or DEFAULT_PHASE).strip().strip("/\\")
        m = _PHASE_RE.match(raw)

        if m:
            no, label = int(m.group(1)), m.group(2)
        else:
            no, label = self._next_phase_no(), raw

        label = _BAD_NAME.sub("", label).strip("-_") or DEFAULT_PHASE
        no = max(no, 1)

        d = self.root / f"{no:02d}-{label}"
        if not d.is_dir():
            # 同号不同名（项目经理改主意了）→ 让号，别覆盖别人的目录
            same_no = [p for p in self._phases() if p.name.startswith(f"{no:02d}-")]
            if same_no:
                no = self._next_phase_no()
                d = self.root / f"{no:02d}-{label}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _next_phase_no(self) -> int:
        best = 0
        for p in self._phases():
            m = _PHASE_RE.match(p.name)
            if m:
                best = max(best, int(m.group(1)))
        return best + 1

    def next_step(self) -> int:
        """
        下一个交接步骤号。**全项目一条线**（不是每个阶段各数各的）——
        report-03 / instruction-03 / .approval-03 说的是同一件事，
        跨阶段也不该重号。
        """
        best = 0
        if self.root.is_dir():
            for f in self.root.rglob("*.md"):
                m = _STEP_RE.match(f.name)
                if m:
                    best = max(best, int(m.group(1)))
        return best + 1

    # ── 写文件（B-1） ──

    def instruction_path(
        self, *, step: int, target: str, keyword: str, phase_dir: Path | None = None
    ) -> Path:
        d = phase_dir or self.current_phase()
        return d / f"instruction-{step:02d}-{target}-{keyword}.md"

    def approval_path(self, *, step: int, phase_dir: Path | None = None) -> Path:
        d = phase_dir or self.current_phase()
        return d / f".approval-{step:02d}.md"

    def write_instruction_projection(self, path: Path, markdown: str) -> Path:
        """Persist a Markdown display projection of the shared TaskEnvelope goal."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(markdown).rstrip() + "\n", encoding="utf-8")
        return path

    def write_instruction(
        self,
        *,
        step: int,
        target: str,
        keyword: str,
        phase_dir: Path | None = None,
        report_ref: str = "",
        background: str = "",
        previous: str = "",
        task: str = "",
        inputs: str = "",
        acceptance: str = "",
        notes: str = "",
        related_knowledge: str = "",
    ) -> Path:
        """项目经理派活 → instruction-{序号}-{Agent}-{关键词}.md

        [v0.42] ``related_knowledge`` 是 **Harness 填充**的「相关知识」区块
        （指令条件化注入，设计报告 §4.4）：对指令正文做资产匹配，把 top-N 命中的
        L0 索引行附在指令末尾。理由是 v0.23 的那条洞见——模型 composing 前读到的
        最后的东西权重最大，指令正文比 system prompt 离行动近得多。
        没有命中就不写这一段（不给 Worker 塞空段落）。
        """
        d = phase_dir or self.current_phase()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"instruction-{step:02d}-{target}-{keyword}.md"

        front = {"from": msg("hd.015"), "to": target, "created": _today()}
        if report_ref:
            front["report_ref"] = report_ref

        body = [_yaml(front), "", msg("hd.016", keyword=keyword), ""]
        body += _sections(_INSTRUCTION_SECTIONS, {
            "background": background,
            "previous": previous,
            "task": task,
            "inputs": inputs,
            "acceptance": acceptance,
            "notes": notes,
        })
        related = related_knowledge.strip()
        if related:
            body += [msg("hd.017"), "", related, ""]
        path.write_text("\n".join(body).rstrip() + "\n", "utf-8")
        return path

    def write_report(
        self,
        *,
        step: int,
        agent_id: str,
        keyword: str,
        phase_dir: Path | None = None,
        status: str = "completed",
        report_hash: str = "",
        instruction_ref: str = "",
        completed_what: str = "",
        matches_instruction: str = "",
        artifacts: list[str] | None = None,
        issues: str = "",
        self_check: str = "",
        knowledge_used: list[str] | None = None,
        knowledge_not_helpful: list[str] | None = None,
        knowledge_suggest: str = "",
        task_id: str = "",
        run_id: str = "",
        delivery_id: str = "",
        completion_id: str = "",
        effect_id: str = "",
        author: str = "",
        source_kind: str = "",
        status_reason: str = "",
        gaps: list[str] | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> Path:
        """把 Runtime Delivery/失败边界写成 report-{序号}-{Agent}-{关键词}.md。

        [v1.0.24.3 INT/EXT 拆分] 一次交付写两份：
          · EXT —— handoffs/{phase}/report-{step}-{agent}-{kw}.md（文件名保持旧规范，
            所有正则/扫描零破坏）。只带最小安全头（report_hash/status/delivery_id/
            task_id/run_id/created），正文从「一、我完成了什么」开始，无「零、Completion
            状态」段。用户「报告/交接」tab、PM read_report、知识图谱、T1 蒸馏全读它。
          · INT —— {internal_workspace}/audit/{phase}/report-INT-{step}-{agent}-{kw}.md
            （audit/ 在 handoffs/ 树外，任何 rglob/扫描不可达）。全字段 YAML 头 + 零~六段
            全文，仅供软件内部溯源/审计。
        返回值始终是 EXT 路径——所有现有调用方（审批回链/知识更新/蒸馏/血缘校验）的
        对外契约不变。
        """
        d = phase_dir or self.current_phase()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"report-{step:02d}-{agent_id}-{keyword}.md"

        # 未知状态必须 fail-closed；绝不能把适配错误伪装成已完成。
        st = status if status in _STATUSES else (
            "FAILED" if completion_id or effect_id else "failed"
        )
        prov = (
            normalize_provenance(provenance, legacy_if_missing=True).to_dict()
            if provenance is not None
            else unknown_legacy_provenance().to_dict()
        )
        front = {
            "from": agent_id,
            "status": st,
            "created": _today(),
            "provenance": str(prov.get("status") or "unknown_legacy"),
            "provenance_schema_version": str(prov.get("provenance_schema_version") or 1),
            "provenance_id": str(prov.get("provenance_id") or "unknown_legacy"),
            "build_id": str(prov.get("build_id") or ""),
            "git_commit": str(prov.get("git_commit") or ""),
            "runtime_schema_version": str(prov.get("runtime_schema_version") or ""),
            "harness_schema_version": str(prov.get("harness_schema_version") or ""),
            "prompt_bundle_version": str(prov.get("prompt_bundle_version") or ""),
            "migration_epoch": str(prov.get("migration_epoch") or 0),
            "build_manifest_sha256": str(prov.get("build_manifest_sha256") or ""),
            "source_tree_sha256": str(prov.get("source_tree_sha256") or ""),
            "schema_registry_sha256": str(prov.get("schema_registry_sha256") or ""),
            "recorded_at": str(prov.get("recorded_at") or ""),
        }
        if prov.get("startup_id"):
            front["startup_id"] = str(prov["startup_id"])
        if task_id:
            front["task_id"] = str(task_id)
        if run_id:
            front["run_id"] = str(run_id)
        if delivery_id:
            front["delivery_id"] = str(delivery_id)
        if completion_id:
            front["completion_id"] = str(completion_id)
        if effect_id:
            front["projection_effect_id"] = str(effect_id)
        if author:
            front["author"] = str(author)
        if source_kind:
            front["source_kind"] = str(source_kind)
        if status_reason:
            front["status_reason"] = str(status_reason)
        if report_hash:
            front["report_hash"] = report_hash
        if instruction_ref:
            front["instruction_ref"] = instruction_ref

        files = artifacts or []
        artifacts_md = "\n".join(f"- `{a}`" for a in files) if files else msg("hd.013")

        gap_rows = [str(item).strip() for item in (gaps or []) if str(item).strip()]

        # [v1.0.24.3] 正文组装分两份：INT 带「零、Completion 状态」段（审计），EXT 不带（LLM 阅读）。
        body_int = [
            _yaml(front), "", msg("hd.019", keyword=keyword), "",
            msg("hd.020"), "",
            f"- status: `{st}`",
            f"- author: `{author or 'legacy'}`",
            f"- reason: {status_reason.strip() or msg('hd.013')}",
            "- gaps:",
            *(f"  - {item}" for item in gap_rows),
            *( [f"  - {msg('hd.013')}"] if not gap_rows else [] ),
            "",
        ]
        # EXT 最小安全头：只留血缘/反查/图谱必需的字段（审计字段全归 INT）。
        front_ext: dict[str, str] = {"created": _today()}
        if report_hash:
            front_ext["report_hash"] = report_hash
        if st:
            front_ext["status"] = st
        if delivery_id:
            front_ext["delivery_id"] = str(delivery_id)
        if task_id:
            front_ext["task_id"] = str(task_id)
        if run_id:
            front_ext["run_id"] = str(run_id)
        body_ext = [
            _yaml(front_ext), "", msg("hd.019", keyword=keyword), "",
        ]
        # 公共正文：一~六段（含知识引用），两份共用。
        public_sections = _sections(_REPORT_SECTIONS, {
            "completed_what": completed_what,
            "matches_instruction": matches_instruction,
            "artifacts_md": artifacts_md,
            "issues": issues,
            "self_check": self_check,
        })
        # 第六段「知识引用」（强制引用协议，闭环前半）。
        #   Engine 从 Runtime/知识账本填充，三个字段永远都在；「（未引用）」也是
        #   一条诚实信号（matched_never_used 靠它成立）。
        used = [str(a) for a in (knowledge_used or []) if str(a).strip()]
        nh = [str(a) for a in (knowledge_not_helpful or []) if str(a).strip()]
        knowledge_sections = [
            msg("hd.026"), "",
            "- used: " + (", ".join(used) if used else msg("hd.014")),
            "- not_helpful: " + (", ".join(nh) if nh else msg("hd.013")),
            "- suggest: " + (knowledge_suggest.strip() or msg("hd.013")),
            "",
        ]
        body_int += public_sections + knowledge_sections
        body_ext += public_sections + knowledge_sections
        path.write_text("\n".join(body_ext).rstrip() + "\n", "utf-8")

        # [v1.0.24.3] INT 落盘：audit/ 与 handoffs/ 平级，全字段，仅供内部溯源。
        #   命名 report-INT-… 且位于 handoffs/ 树外 → 前端 tab / read_report /
        #   知识图谱 / 蒸馏的所有 rglob 均不可达（审计报告.lock 无锁，写失败不阻断交付）。
        try:
            audit_dir = self.audit_dir / d.name
            audit_dir.mkdir(parents=True, exist_ok=True)
            int_path = audit_dir / f"report-INT-{step:02d}-{agent_id}-{keyword}.md"
            int_path.write_text("\n".join(body_int).rstrip() + "\n", "utf-8")
        except OSError:
            # INT 是审计副本，写失败只记日志，不阻断 EXT 交付主链。
            import logging
            logging.getLogger(__name__).exception(
                "[handoff] INT 审计报告落盘失败（不影响 EXT 交付）：%s", d.name,
            )
        return path

    def write_approval(
        self,
        *,
        step: int,
        decision: str,
        target: str,
        keyword: str,
        phase_dir: Path | None = None,
        instruction_file: str = "",
        report_ref: str = "",
        instruction_text: str = "",
        task_id: str = "",
        task_envelope_ref: str = "",
        provenance: Mapping[str, Any] | None = None,
    ) -> Path:
        """
        Harness 自动写的审批记录 → `.approval-{序号}.md`（双向链接）。

        ★ **拒绝也写。** 「用户不同意」是这个项目真实发生过的一件事，
          它和「用户同意了」一样重要——三个月后回头看，你想知道的正是
          「当初为什么没往那条路走」。
          （但被拒的那一步**不写 instruction 文件**：那件事没有发生，
            工作目录是用户自己的文件夹，不该留下没发生过的东西。）
        """
        d = phase_dir or self.current_phase()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f".approval-{step:02d}.md"

        front = {"step": f"{step:02d}", "decision": decision,
                 "target": target, "created": _today()}
        if task_id:
            front["task_id"] = str(task_id)
        if task_envelope_ref:
            front["task_envelope_ref"] = str(task_envelope_ref)
        if provenance is not None:
            prov = normalize_provenance(provenance, legacy_if_missing=True).to_dict()
            front.update({
                "provenance": str(prov.get("status") or "unknown_legacy"),
                "provenance_schema_version": str(prov.get("provenance_schema_version") or 1),
                "provenance_id": str(prov.get("provenance_id") or "unknown_legacy"),
                "build_id": str(prov.get("build_id") or ""),
                "git_commit": str(prov.get("git_commit") or ""),
                "runtime_schema_version": str(prov.get("runtime_schema_version") or ""),
                "harness_schema_version": str(prov.get("harness_schema_version") or ""),
                "prompt_bundle_version": str(prov.get("prompt_bundle_version") or ""),
                "migration_epoch": str(prov.get("migration_epoch") or 0),
                "build_manifest_sha256": str(prov.get("build_manifest_sha256") or ""),
                "source_tree_sha256": str(prov.get("source_tree_sha256") or ""),
                "schema_registry_sha256": str(prov.get("schema_registry_sha256") or ""),
                "recorded_at": str(prov.get("recorded_at") or ""),
            })
        lines = [_yaml(front), "", msg("hd.027", step=f"{step:02d}", keyword=keyword), ""]
        lines += [msg("hd.028", decision=_zh_decision(decision)), msg("hd.029", target=target)]
        if instruction_file:
            lines.append(msg("hd.030", file=instruction_file))
        if report_ref:
            lines.append(msg("hd.031", ref=report_ref))
        lines += ["", msg("hd.035"), "", (instruction_text.strip() or msg("hd.013")), ""]
        path.write_text("\n".join(lines).rstrip() + "\n", "utf-8")
        return path

    def link_report_into_approval(self, step: int, report_file: str,
                                  phase_dir: Path | None = None) -> None:
        """
        报告交上来了 → 回头在审批记录里补一条链接（**双向**才算双向）。

        写不进去也不许炸：审批记录是给人看的账，不是流程的一环。
        """
        d = phase_dir or self.current_phase()
        path = d / f".approval-{step:02d}.md"
        if not path.is_file():
            return
        try:
            text = path.read_text("utf-8", errors="replace")
            if report_file in text:
                return
            text = text.rstrip() + (
                f"\n\n{msg('hd.036')}\n\n{msg('hd.037', file=report_file)}\n")
            path.write_text(text, "utf-8")
        except OSError:
            pass

    # ── 扫描（B-2 ②） ──

    def reports(self) -> list[Path]:
        """所有报告文件（含历史阶段），按名字排序。"""
        if not self.root.is_dir():
            return []
        return sorted(self.root.rglob("report-*.md"))

    def instructions(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(self.root.rglob("instruction-*.md"))

    def rel(self, path: Path) -> str:
        """给 LLM 看的路径：一律 `handoffs/03-后端/report-03-fe_1-x.md` 这个形状。"""
        try:
            return "handoffs/" + path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def find(self, ref: str) -> Path | None:
        """
        按「引用」找一个交接文件。ref 可以是：
          · 相对路径 `handoffs/03-后端/report-03-fe_1-x.md`
          · 文件名   `report-03-fe_1-x.md`
          · 报告号   `a1b2c3…`（老的 handoffs/{hash}.md，以及新报告 frontmatter 里的号）
        找不到 → None。**绝不抛异常**：模型引用错一个名字，不该把引擎带走。
        """
        if not isinstance(ref, str) or not ref.strip():
            return None
        raw = ref.strip().replace("\\", "/")
        raw = raw[len("handoffs/"):] if raw.startswith("handoffs/") else raw

        cand = (self.root / raw)
        if cand.is_file():
            return cand

        name = Path(raw).name
        if self.root.is_dir():
            for f in self.root.rglob("*.md"):
                if f.name == name or f.stem == Path(name).stem:
                    return f
            # 老格式：handoffs/{hash}.md；以及新报告 frontmatter 里的 report_hash
            for f in self.root.rglob("*.md"):
                try:
                    head = f.read_text("utf-8", errors="replace")[:400]
                except OSError:
                    continue
                if f"report_hash: {name}" in head:
                    return f
        return None


def _zh_decision(decision: str) -> str:
    from .i18n_backend import msg  # 局部导入：避免模块级语言固化
    return {
        "approved": msg("hd.038"),
        "rejected": msg("hd.039"),
        "timeout": msg("hd.040"),
        "cancelled": msg("hd.041"),
    }.get(decision, decision)
