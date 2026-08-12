# knowe v0.22 — 角色能力库
"""
roles.py — 24 个角色，每个「擅长什么 / **不适合什么**」。

## 为什么要有这个

用户要「浏览小红书博主主页截图」「搜索下载照片」，项目经理把活派给了
**Shiloh · 技术写作**。技术写作是写文档、写教程、写 API 参考的——不是爬网页的。

项目经理眼里的团队长这样：

    当前成员：Shiloh（技术写作）、…

**就这么多。** 24 个角色标签，24 个盲盒。他没有任何信息能判断
「技术写作适不适合爬网页」，于是他挑了名字里带「技术」的那个。

## 为什么不是「角色太少」，而是「项目经理不认识角色」

PRD 提到可以从 agency-agents 引进 100+ 个角色。我没有引，理由有三条，
第三条是决定性的：

1. **加角色不解决这个 bug。** 24 个盲盒变成 124 个盲盒，只会更难挑。
   缺的从来不是角色，是**判断依据**。
2. **需要的角色其实已经在了。** 「爬网页 / 下载图片」→ 后端 或 数据分析，
   两个都在这 24 个里。soul 里甚至早就写着「做爬虫的归后端」——
   只是那句话埋在第 154 行，而项目经理根本没往那儿看。
3. **★ 前缀是和前端的契约，动不了。** 见 tools_knowe.KNOWN_ROLES 的注释：
   「前端（state.ts DEFAULT_ROLE_TYPES）：同一张表，前缀 → 角色。两边必须对得上。」
   加一个 `data-engineer(de)` 就得改 state.ts —— 而「不改前端」是硬约束。
   硬塞进来的新角色，界面会认不出，花名册上又会冒出一排「Agent」
   （那正是 KNOWN_ROLES 当初要解决的问题）。

所以：**不加角色，加认识。** 描述取自 PRD 给的 agency-agents 那套角色库
（`description` / `vibe` 字段），压缩成中文的一行「擅长」+ 一行「不适合」。

## ★ 为什么必须有「不适合」

只写「擅长」是不够的，而且这一点很反直觉。

    技术写作：擅长 写文档、教程、API 参考

项目经理读到这句，要派「爬网页」的活时，它会**自己圆过来**：
「爬网页也是搜集素材，素材是写文档的一部分，那就他吧。」
——LLM 极其擅长把任何任务论证成任何角色的分内事。

    技术写作：擅长 写文档、教程、API 参考
              **不适合** 爬虫抓取、数据采集、跑构建、写业务代码

这就圆不过去了。**排除比包含更有判别力**：一句「不适合」划出的边界，
比三句「擅长」都管用。

## 用在哪（★ 刻意不进项目经理的系统提示词）

问题二说得很清楚：项目经理的提示词已经 ~10,000 字，核心规则被稀释了。
再往里塞一张 24 行的角色表，是在给问题二火上浇油。所以这份表**按需送达**：

  · `propose_agents` 的**工具描述** → 完整目录。模型是在**决定加人的那一刻**
    读工具 schema 的，这正是「该挑哪个角色」的决策点。
    （顺带把 soul 里那份光秃秃的角色清单删掉 —— 系统提示词反而**更短**了。）
  · `engine._team_ctx()` 的花名册行 → 每人一句。这是**派活那一刻**项目经理看的地方，
    每人一行，按当前花名册完整呈现。
  · `engine._identity_block()` → 成员自己的「我是谁」，让他按这个角色的方式想问题。

同一份真源，三个消费者，各取所需 —— 和 capabilities.py 一个路数。

## 和 capabilities.py 的分工（别搞混）

  · capabilities.py：**工具箱** —— 每个成员能做什么。**人人完全一样。**
  · roles.py（这里）：**判断力** —— 哪个角色适合做什么。**因人而异。**

v0.21 我在 capabilities 里写了一句「角色只影响他怎么想问题，不影响他能做什么」。
那句话是对的，但它**只说了一半**，而项目经理把它读成了「角色无所谓」——
于是把爬虫派给了技术写作（反正工具箱一样嘛）。
这一版两边都补上另一半：**工具一样，脑子不一样；派活挑的是脑子，不是工具箱。**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .i18n_backend import msg

log = logging.getLogger("knowe.roles")


@dataclass(frozen=True)
class RoleProfile:
    """一个角色的判断力边界。`prefix` / `label` 必须和 KNOWN_ROLES 对得上。"""

    prefix: str
    label: str
    good_at: str
    not_for: str


#: 24 个角色的能力边界。
#:
#: 键 = id 前缀 = KNOWN_ROLES 的键（有测试守着两张表严格一致：
#: 少一个 → 那个角色又变回盲盒；多一个 → 前端认不出的幽灵角色）。
#:
#: 描述来源：agency-agents（PRD 提供）的对应角色文件，压成中文一行。
ROLE_PROFILES: dict[str, RoleProfile] = {
    "fe": RoleProfile(
        "fe", "前端",
        "页面与组件实现、交互与样式、前端性能、浏览器里的调试与联调",
        "服务端架构、数据管道、部署运维",
    ),
    "be": RoleProfile(
        "be", "后端",
        "服务端与 API、数据库读写、**网页抓取与爬虫**、自动化脚本、"
        "第三方集成、后台任务、把数据取回来落盘",
        "视觉设计、市场文案",
    ),
    "pm": RoleProfile(
        "pm", "产品",
        "需求梳理、优先级、用户故事、验收标准、把模糊的想法变成能验收的条目",
        "写代码、跑构建、做设计稿",
    ),
    "qa": RoleProfile(
        "qa", "测试",
        "测试用例、端到端自动化、回归、缺陷复现、按验收标准逐条核对",
        "从零实现功能、架构设计",
    ),
    "ux": RoleProfile(
        "ux", "UI/UX 设计",
        "信息架构、交互流程、视觉规范、可用性评审、设计稿",
        "后端逻辑、数据处理、部署",
    ),
    "da": RoleProfile(
        "da", "数据分析",
        "**数据采集与清洗**、统计与建模、图表与报表、指标口径、从一堆原始数据里得出结论",
        "前端实现、系统架构",
    ),
    "devops": RoleProfile(
        "devops", "运维",
        "CI/CD、部署、容器与环境、依赖安装、构建流水线、把东西跑起来",
        "产品决策、市场文案",
    ),
    "sec": RoleProfile(
        "sec", "安全",
        "威胁建模、代码安全审计、漏洞与权限、依赖风险、合规检查",
        "功能开发、界面实现",
    ),
    "ml": RoleProfile(
        "ml", "AI/机器学习",
        "模型选型与训练、推理与部署、评测、提示工程、向量检索与 RAG",
        "前端实现、运维值班",
    ),
    "mobile": RoleProfile(
        "mobile", "移动端",
        "iOS/Android 实现、跨端与适配、移动端性能、上架流程",
        "服务端架构、数据管道",
    ),
    "game": RoleProfile(
        "game", "游戏",
        "玩法与关卡设计、游戏循环与数值、引擎实现",
        "企业系统、数据管道",
    ),
    "gis": RoleProfile(
        "gis", "地理信息",
        "地图与空间数据、坐标系与投影、空间分析、地理可视化",
        "通用后端、市场文案",
    ),
    "mkt": RoleProfile(
        "mkt", "营销",
        "增长策略、内容与渠道、转化漏斗、社媒运营、竞品与市场调研",
        "写代码、系统架构",
    ),
    "fin": RoleProfile(
        "fin", "金融/财务",
        "财务建模与预测、成本与估值、情景分析、把数字变成决策依据",
        "写代码、界面实现",
    ),
    "hc": RoleProfile(
        "hc", "医疗",
        "临床证据梳理、医学合规、健康数据的解读与边界",
        "通用软件开发",
    ),
    "edu": RoleProfile(
        "edu", "学术/教育",
        "文献检索与综述、研究方法、教学设计、引用规范",
        "工程实现、部署",
    ),
    "ar": RoleProfile(
        "ar", "空间计算",
        "XR / 3D 场景、空间交互、visionOS 与头显端实现",
        "常规 Web 后端、数据分析",
    ),
    "sup": RoleProfile(
        "sup", "技术支持",
        "问题排查与复现、用户答疑、工单与 FAQ、把用户的话翻译成可查的线索",
        "架构设计、从零开发新功能",
    ),
    "sre": RoleProfile(
        "sre", "站点可靠性",
        "可观测性与告警、SLO 与错误预算、故障演练与复盘、容量与稳定性",
        "产品设计、市场文案",
    ),
    "db": RoleProfile(
        "db", "数据库",
        "表结构设计、索引与查询优化、迁移与备份、慢查询排查",
        "前端实现、市场文案",
    ),
    "arch": RoleProfile(
        "arch", "架构",
        "系统设计与技术选型、模块边界与依赖、架构评审、技术债权衡",
        "具体页面实现、日常运维值班",
    ),
    "writer": RoleProfile(
        "writer", "技术写作",
        "开发文档、教程、README、API 参考、发布说明——把复杂的东西写清楚",
        # ★ 这一行直接对着 v0.22 那个 bug：爬网页截图的活被派给了技术写作。
        "**爬虫与网页抓取**、数据采集、跑构建、写业务代码、系统排障",
    ),
    "media": RoleProfile(
        "media", "音视频",
        "音视频处理与转码、剪辑与字幕、播放与推流",
        "后端架构、数据分析",
    ),
    "legal": RoleProfile(
        "legal", "法务/合规",
        "条款与合同、合规审查、隐私与数据边界、开源许可证",
        "写代码、部署",
    ),
}


def profile_for(role_or_prefix: str) -> RoleProfile | None:
    """按前缀或中文角色名查。两样都认——调用方手里有时是 id，有时是花名册上的标签。"""
    key = (role_or_prefix or "").strip()
    if not key:
        return None
    hit = ROLE_PROFILES.get(key)
    if hit is not None:
        return hit
    for prof in ROLE_PROFILES.values():
        if prof.label == key:
            return prof
    return None


def _localized(prof: RoleProfile) -> tuple[str, str, str]:
    """
    [语言化] 按当前语言取角色的 (label, good_at, not_for)。

    ROLE_PROFILES 保持中文原文（zh 基准，profile_for 的中文标签反查依赖它）；
    en 模式查 locales/en.json 的 roles.profiles.<prefix>.* 翻译表，
    查不到（表缺 key / msg 返回 key 自身）时回退中文原文。
    """
    label = msg(f"roles.profiles.{prof.prefix}.label")
    good = msg(f"roles.profiles.{prof.prefix}.good_at")
    not_for = msg(f"roles.profiles.{prof.prefix}.not_for")
    if label.startswith("roles.profiles."):
        label = prof.label
    if good.startswith("roles.profiles."):
        good = prof.good_at
    if not_for.startswith("roles.profiles."):
        not_for = prof.not_for
    return label, good, not_for


def localized_label(prof: RoleProfile) -> str:
    """[语言化] 当前语言下的角色标签（en: Frontend / zh: 前端）。显示层统一用它。"""
    return _localized(prof)[0]


def profile_for_agent_id(agent_id: str) -> RoleProfile | None:
    """`fe_1` → 前端。id 的头一截就是前缀，这是 v0.10a 定下的规矩。"""
    return ROLE_PROFILES.get((agent_id or "").split("_")[0])


def roster_hint(role: str) -> str:
    """
    花名册上跟在名字后面的那半句。**必须短** —— 它每一轮都在项目经理眼前，
    而且最多乘以 8 个人。长了就是在给问题二（提示词膨胀）添柴。
    """
    prof = profile_for(role)
    if prof is None:
        return ""
    _, good, not_for = _localized(prof)
    return msg("roles.template.roster_hint", good=_first_clause(good), bad=_first_clause(not_for))


def _first_clause(text: str) -> str:
    """取头两项就够认人了，不用把整段搬到花名册上。"""
    parts = [p for p in text.replace("**", "").split("、") if p.strip()]
    return "、".join(parts[:2])


def catalog_for_tool() -> str:
    """
    `propose_agents` 工具描述里的完整目录。

    放在**工具描述**而不是系统提示词里，是这一版的一个刻意选择：
    模型是在「我要加人」的那一刻读这个 schema 的 —— 那正是「挑哪个角色」的决策点。
    而系统提示词里那份光秃秃的清单可以就此删掉，**项目经理的提示词反而变短了**。
    一举两得：问题四拿到了更多信息，问题二少扛了一段。
    """
    lines = []
    for prof in ROLE_PROFILES.values():
        label, good, bad = _localized(prof)
        good = good.replace("**", "")
        bad = bad.replace("**", "")
        lines.append(msg("roles.template.catalog_line", label=label, prefix=prof.prefix, good=good, bad=bad))
    return "\n".join(lines)


def identity_block(agent_id: str, role: str) -> str:
    """
    成员自己的「我是这个角色」。给的是**看问题的方式**，不是权限。

    ★ 注意这里绝不写「你不能做 X」：成员的工具箱和别人完全一样（见 capabilities.py），
      「不适合」说的是**该由谁来做更好**，不是**你被禁止**。
      写成禁令的话，一个技术写作接到爬虫任务会当场撂挑子——
      而用户要的是活干完，不是一场关于岗位职责的辩论。
    """
    prof = profile_for_agent_id(agent_id) or profile_for(role)
    if prof is None:
        return ""
    label, good, not_for = _localized(prof)
    return (
        msg("roles.template.identity.specialty", label=label)
        + msg("roles.template.identity.good_at", good=good.replace("**", ""))
        + msg("roles.template.identity.not_for", not_for=not_for.replace("**", ""))
        + msg("roles.template.identity.tail")
    )


__all__ = [
    "ROLE_PROFILES",
    "RoleProfile",
    "catalog_for_tool",
    "identity_block",
    "localized_label",
    "profile_for",
    "profile_for_agent_id",
    "roster_hint",
]
