<!--
  文档：EN 术语对照表与英文文件命名规范（写作硬约束）
  所属：Knowe 桌面应用 · 技术文档
  对应：docs/CN/50-03-术语表.md + docs/CN/文档结构规划.md（§0.2 语言策略、§3 slug 建议、§4 内容要点）
  状态：规范稿（只出规范，不写 EN 正文）
  日期：2026-08
-->

# Knowe 桌面应用 · EN 术语对照表与英文文件命名规范

> **摘要**：本文档是英文平行翻译与 EN 页面撰写时的**唯一术语口径与命名硬约束**。包含三部分：① 中文 → 英文术语对照表（覆盖角色、机制、界面功能、数据费用、技术名词、章节导航）；② 专有名词保留、大小写与单复数约定；③ 35 个页面的英文文件名方案与 H1 标题规范，与 `docs/CN/` 目录树一一对应。
>
> **权威层级**：产品内置英文字符串 **>** 本表 **>** 译者个人判断。冲突时以产品为准，并回报更新本表。
>
> **适用范围**：`docs/EN/` 下全部页面（当前 35 页 + 后续新增页）；不含研发内部文档。

**在本页**

- [0. 本文件的定位与用法](#0-本文件的定位与用法)
- [1. 语言与翻译总则](#1-语言与翻译总则)
- [2. 术语对照表](#2-术语对照表)
- [3. 专有名词、大小写与单复数](#3-专有名词大小写与单复数)
- [4. EN 文件命名方案](#4-en-文件命名方案)
- [5. 术语入库与维护流程](#5-术语入库与维护流程)

---

## 0. 本文件的定位与用法

- **它是什么**：EN 写作团队的工作规范，等价于 CN 侧的「文档结构规划」；不是最终发布页。
- **它从哪来**：依据 `docs/CN/50-03-术语表.md`（38 个统一术语）与 `docs/CN/文档结构规划.md`（§0.2 语言策略、§3 slug 建议、§4 各章内容要点）提炼、整理、补充而成。
- **怎么用**：翻译/撰写任何 EN 页面之前，先查 §2 术语表；文件名与 H1 严格按 §4 执行；遇到新词按 §5 入库。
- **「只出规范，不写正文」**：本文档定义术语、命名与写作约束；35 个 EN 页面正文已按此规范撰写完成。

---

## 1. 语言与翻译总则

1. **双语同构**：产品界面与提示词已内置中/英双语。文档以中文为基准撰写、英文平行翻译，CN 与 EN 目录树一一对应、同步发布（规划 §0.2）。CN 每章完成后随即翻译，避免积压（规划 §6.2 M5）。
2. **角色名随语言、成员名不翻译**：角色名（知知、项目经理、成员等）随界面语言翻译；用户自定义的成员名字在任何语言下都保留原样（规划 §2.2）。
3. **界面字符串优先**：英文正文中出现界面文案时，一律使用产品内置英文；本表已按此口径给出推荐值。拿不准时以产品实际显示为准。
4. **一条术语一个译名**：同一概念全文只用一个英文表达，不因语境换词（见 §2 备注）。
5. **中文词不进 EN 正文**：EN 页面不得出现中文术语（如「审批卡」「花名册」），一律用 §2 的标准英文；专有名词按 §3.1 保留原名。
6. **不逐字直译**：高频误译清单见 §3.4，翻译前先过一遍。

---

## 2. 术语对照表

> 列说明：**中文原文**（CN 文档/产品用词）→ **标准英文**（EN 正文唯一译名）→ **备注**（用法、别名、大小写、首现展开等）。
> 表格未列出的词：按产品 UI 英文文案直用；确实没有的，按 §5 入库。

### 2.1 核心角色

| 中文原文 | 标准英文 | 备注 |
|:--|:--|:--|
| 知知 | **Zinnia** | 平台级 AI 接待，固定私聊窗口。专有名词，**不翻译**（≠ Knowledge / Kiki）。CN 正文写「知知（Zinnia）」，EN 正文一律 Zinnia |
| 项目经理 | **Coordinator** | 产品角色名，大写 C；每个项目一位，只有提议权。泛指「管理者」时用小写 project manager；首现可写 the Coordinator (the project manager) |
| 成员 | **Worker** | 产品角色名，大写 W；24 种角色之一。泛指「团队成员」时用小写 member。复数 workers / members |
| 角色目录 | **Roles Catalog** | 50-01 页标题词。「24 种角色」= the 24 roles / the 24 worker roles |
| 花名册 | **Roster** | 项目内成员实时状态面板（谁在忙/谁闲着/谁已归档）。≠ name list；单数即表集合，写 the roster |
| 归档 | **Archive** | 动词/名词。「减人：归档而不是删除」= Remove a member: archive, not delete；状态「已归档」= Archived |
| 停止 | **Stop** | 中断正在干活的成员；按钮文案 Stop。「行内二次确认，5 秒自动撤回」= inline double confirmation, auto-cancels in 5 seconds |
| 空闲 / 工作中 / 待命 / 已归档 | **Idle / Working / On standby / Archived** | 成员状态四态（规划 §2.2） |
| 擅长 / 不适合 | **Good at / Not suitable for** | 成员的两条边界；角色目录表头建议 Good at / Not suitable for（表格内可简写 Not for） |
| 提议权 / 决定权 | **right to propose / right to decide** | 「只有提议权，没有决定权」= the Coordinator can propose, but you decide |
| 组队 / 团队 | **build the team / the team** | 「组建团队」= build the team / assemble the team |

### 2.2 核心机制（审批 / 派活 / 验收 / 交接 / 花名册）

| 中文原文 | 标准英文 | 备注 |
|:--|:--|:--|
| 审批 | **Approval** | 名词；动词 approve = 批准。「审批机制」= Approval mechanism |
| 审批卡 | **Approval Card** | 建项目/加人/派活/移除四类「人和活」动作的统一卡片。指 UI 元素时 Title Case，泛指时小写 approval card |
| 建群卡（创建项目卡） | **Create Project card** | 知知提议开项目时弹出；字段：项目名 + 工作区目录（必选）。括号内为 CN 别名，EN 正文统一 Create Project card |
| 团队卡（组建团队卡） | **Build Team card** | 项目经理提议加人时弹出；候选成员列表（头像、名字、角色、擅长） |
| 任务卡（派发任务卡） | **Task card** | 派活审批卡；三个字段 = 派给谁 / 任务指令 / 项目经理附言（who / the task / the Coordinator's note），审批期间可反馈修改意见 |
| 四终态 | **four final states** | 已确认 / 已拒绝 / 已超时 / 已取消 → **Approved / Rejected / Timed out / Canceled**（CN「已确认」在审批语境即批准；以产品文案为准）。倒计时以服务端时钟为准 = server-side clock |
| 审批超时 | **Approval timeout** | 倒计时走完自动撤回（≠ 拒绝）；时限 5 / 10 / 30 / 60 / 180 / 300 秒或不限时 |
| 倒计时 | **Countdown** | 恢复卡与审批卡都有倒计时 |
| 派活 | **assign (a task)** | 动词 assign。「派活与验收」页名 = Assign and Accept（见 §4.2） |
| 验收 | **Acceptance**（名词）/ **verify**（动词） | 页面标题用 Acceptance；「项目经理核对成果」= the Coordinator verifies the deliverable（读文件验证，不轻信报告） |
| 交接 | **Handoff** | 现 35 页 CN 正文未出现此词（检索 0 命中）；描述成员间/项目间任务与上下文移交时使用。勿写成动词化的 hand over |
| 移除成员 | **Remove a member** | 审批卡四类之一（加人 / 派活 / 移除 / 建群） |
| 二次确认 | **double confirmation** | 「行内二次确认」= inline double confirmation |

### 2.3 界面与功能概念

| 中文原文 | 标准英文 | 备注 |
|:--|:--|:--|
| 项目 | **Project** | 最小工作单元：一个群聊 + 一个本地工作区目录，缺一不可 |
| 工作区（目录） | **Workspace (directory)** | 建项目时自选的本地目录；AI 只能读写这里的文件 |
| 沙箱 | **Sandbox** | 工作区目录的通俗说法；「AI 的沙箱」= the AI's sandbox；附件是唯一例外入口 |
| 目录失效 | **Missing directory** | 项目目录被移走/改名/删除后的状态；界面提示如 Your workspace directory is missing |
| 恢复卡 | **Recovery card** | 聊天流内带 5 分钟倒计时的恢复卡片；改名或重选目录 = rename it or pick a new directory |
| 数据目录 | **Data directory** | 安装目录下的 `data/`（应用数据）与 `Logs/`（日志） |
| 群聊 | **Group chat** | 项目群聊 |
| 私聊 | **DM（direct message）** | 首现展开 direct message (DM)；「群内私聊」= in-project DM（通道名 `dm:project:member` 原样保留） |
| 平台私聊 | **Platform DM** | 与知知的固定私聊窗口 |
| 私聊不隐秘 | **DMs are not private** | 边界说明标题；「私聊不是秘密通道」= a DM is not a secret channel（内容写回项目记忆，项目经理始终知道） |
| 消息 | **Message** | |
| 引用 / 转发 / 收藏 / 复制 | **Quote / Forward / Favorite / Copy** | 消息四类操作；「收藏视图」= Favorites view |
| 乐观发送（三态） | **Optimistic send (three states)** | 待确认 / 已送达 / 发送存疑 → Pending / Delivered / Uncertain (⚠) |
| 草稿 | **Draft** | 按会话保存 |
| @ 提及 | **@mention** | 提及成员 |
| 文件卡片 | **File card** | 聊天流中文件（附件或成员成果）的入口 |
| 附件 | **Attachment** | 点选或拖拽；「只有经主进程亲眼确认并签名的路径才被读取」= only paths seen and signed by the main process are read（签名护栏 = signed-path guard） |
| 多模态 | **Multimodal** | 图片直读；其余格式打包为文件内容块 |
| 文件预览窗口 | **File preview window** | 独立窗口、多标签页 |
| 在文件管理器中显示 | **Reveal in File Explorer** | 预览窗口内按钮 |
| 输入区 | **Composer / input box** | 消息输入区；Ctrl/⌘+Enter 发送 = send with Ctrl/⌘+Enter，Enter 换行 |
| 流式输出 | **Streaming output** | 成员开工时的流式过程 |
| 推理面板 | **Reasoning panel** | 可见推理过程 |
| 工作阶段 | **Work stage** | 成员工作阶段指示 |
| 时间分隔线 | **Time divider** | 消息流内 |
| 记忆 | **Memory** | 项目级过程记录；三层 = recent activity / rolling summary / historical activity segments（最近活动 / 滚动摘要 / 历史活动段），自动发生、跨回合保持 |
| 上下文 | **Context** | 跨回合保持；「上下文窗口」= context window |
| 知识库 | **Knowledge base** | |
| 知识资产 | **Knowledge asset** | 五类：preferences / practices / pitfalls / facts / decisions（偏好/做法/坑/事实/决策）；界面四标签：conventions / pitfalls / patterns / checklists（约定/坑/模式/清单）。「坑」在两类中都译 pitfalls |
| 全局知识 / 项目知识 | **Global knowledge / Project knowledge** | 两级作用域：全局所有项目可用，项目仅当前项目 |
| 待审 / 生效中 / 已退役 | **Pending review / Active / Retired** | 知识资产生命周期三态 |
| 知识策展 | **Knowledge curation** | 高级操作：批准 approve / 驳回 reject / 退役 retire / 恢复 restore / 彻底删除 permanently delete |
| 证据清单 / 引用轨迹 | **Evidence list / Citation trail** | 卡片显示「被引用 N 次 / 来源 M 份」= cited N times / M sources |
| 技能包 | **Skill pack** | 三类：系统自备（不可变）system-bundled (immutable) / 项目经验（可策展）project-experience (curatable) / 第三方 third-party（独立生命周期） |
| 记忆与知识的分工 | **Memory vs. knowledge** | 记忆 = 过程（process：谁说了什么、做了什么）；知识 = 沉淀（distilled knowledge：可复用的偏好、做法、坑、事实、决策） |
| 全局搜索 | **Global search** | ⌘K；六类目标：conversations / messages / contacts / favorites / knowledge cards / settings（会话/消息/联系人/收藏/知识卡/设置项） |
| 联系人 | **Contacts** | 联系人资料页 = contact profile（擅长领域、当前状态、权限边界说明） |
| 收藏 | **Favorites** | 收藏视图 |
| 连接状态徽章 | **Connection status badge** | 连接中/已连接/重连中/已断开等 → Connecting / Connected / Reconnecting / Disconnected（六态） |
| 重试 | **Retry** | 后端异常时可点「重试」 |
| 后端 | **Backend** | 应用自带后端进程；本地 WS(8080)/HTTP(8081) 端口自动避让 = ports auto-avoid collisions；端口 = port |
| 托盘 | **System tray** | 「关闭到托盘」= close to tray |
| 脱敏 | **Masking** | 把成员内部 id、内部路径打码；动词 mask；正式场合可用 redaction / redact。「哪些内容会被脱敏」= What gets masked |
| 权限边界 | **Permission boundaries** | 沙箱 / 记忆 / 工具三类权限说明 |
| 主模型 | **Primary model** | 全团队默认、首启强制绑定 |
| 辅助模型 | **Fallback model** | 配置后主模型不可用时自动降级（automatic fallback）；字面直译 auxiliary model，推荐 fallback model |
| 按成员绑定模型 | **Per-member model binding** | 个别成员单独指定模型 |
| API Key | **API Key** | 大写；「系统加密落盘」= encrypted at rest with Windows DPAPI；「不写入浏览器存储」= never stored in browser storage |
| 连接测试 | **Connection test** | 首启引导 = first-run setup / first-run model gate |
| Token 用量 | **Token usage** | 用量面板 = usage dashboard；统计卡 = stat cards；趋势图 = trend chart；明细表 = breakdown table（按模型 / 按成员 = by model / by member 双 Tab） |
| 费用（¥） | **Cost (¥)** | 保留 ¥ 符号；首现可注 RMB (¥) |
| 环境变量 | **Environment variables** | 前缀 `KNOWE_*`；按类别组织：backend / terminal / network / browser / memory / skills（后端/终端/网络/浏览器/记忆/技能） |
| 部署形态 | **Deployment modes** | 本地运行形态：自带后端、端口避让、数据目录 |
| 通知 | **Notifications** | 桌面通知 = desktop notifications |
| 外观 | **Appearance** | 深/浅色 = dark/light theme；跟随系统 = follow the system；大字模式 = large text mode |
| 界面语言 | **Interface language** | 中文 / English 即时切换 = instant switching |
| 账户与身份 | **Account & identity** | 昵称 = display name（或 nickname，全文统一一个）；头像 = avatar；关于 = About（版本号 version / 构建信息 build info） |

### 2.4 数据、成本与模型

| 中文原文 | 标准英文 | 备注 |
|:--|:--|:--|
| Token | **Token** | 保留原名，不翻译；大小写见 §3.2 |
| Token 用量 | **Token usage** | 本地统计的模型调用面板；用法见 §2.3 |
| 费用（¥） | **Cost (¥)** | 以人民币计；见 §2.3 |
| 上下文窗口 | **Context window** | 模型输入容量 |
| 用量 | **Usage** | 不可数名词；「用量与费用」= Usage and cost |
| 模型 / 提供方 | **Model / Provider** | 「模型与提供方」= Models and Providers；提供商 = provider |
| 首启引导 | **First-run setup** | 选提供商 → 选模型 → 填 API Key → 连接测试（必须通过）→ 进入 Knowe；不配置完其余 UI 锁定 |

### 2.5 技术名词与格式

| 中文原文 | 标准英文 | 备注 |
|:--|:--|:--|
| Markdown | **Markdown** | 大写 M |
| GFM | **GFM (GitHub Flavored Markdown)** | 表格 / 删除线 / 任务列表 / 自动链接 |
| 公式渲染 | **Formula rendering** | KaTeX；`$` / `$$` / `\[ \]` |
| 代码高亮 + 行号 | **Syntax highlighting with line numbers** | |
| HTML 剥离 | **HTML stripping** | 安全说明：HTML 会被剥离 |
| FAQ | **FAQ** | 全大写；首现可写 Frequently Asked Questions (FAQ) |
| API | **API** | 全大写 |
| 附件格式 | **PDF / Word / Excel / PPT / images / code / Markdown / plain text** | 格式名保留原名，不翻译 |
| 平台与运行时 | **Windows x64 / NSIS / Chromium / Playwright / DeepSeek** | 专有名词保留原名 |
| 更新日志 | **Changelog** | 版本倒序 v1.0.x |
| 版本号 | **Version** | 字符串 v1.0.x 保留小写 v |

### 2.6 章节与导航词（H1 / 侧栏 / 页脚）

| 中文原文 | 标准英文 | 备注 |
|:--|:--|:--|
| 概述 | **Overview** | 00 页 |
| 快速开始 | **Quickstart** | 01 页 |
| 安装与系统要求 | **Installation and System Requirements** | 02 页 |
| 核心概念 | **Core Concepts** | 10 章 |
| 使用指南 | **Guides** | 20 章；全站统一，勿与 User Guide 混用 |
| 配置 | **Configuration** | 30 章 |
| 高级功能 | **Advanced** | 40 章 |
| 参考 | **Reference** | 50 章 |
| 故障排除 | **Troubleshooting** | 60 章 |
| 发布 | **Releases** | 70 章 |
| 支持 | **Support** | 80 章 |
| 安全与隐私 | **Security & Privacy** | 80-01 页 |
| 联系我们 | **Contact Us** | 80-02 页 |
| 术语表 | **Glossary** | 50-03 页 |
| 角色目录 | **Roles Catalog** | 50-01 页 |
| 常见问题 | **FAQ** | 50-04 页 |
| 在本页（页内目录） | **On this page** | 页内 TOC 标题 |
| 上一页 / 下一页 | **Previous / Next** | 页脚导航 |

---

## 3. 专有名词、大小写与单复数

### 3.1 保留原名的术语（不翻译、不改写）

以下词在任何语言正文中**原样保留**，不得意译、不得本地化：

| 词 | 约束 |
|:--|:--|
| **Knowe** | 产品名，恒为 Knowe；不译作「可诺」等 |
| **Zinnia** | 知知的官方英文名（产品内置）；不译作 Knowledge 等 |
| **Coordinator / Worker** | 角色名（见 §2.1）；大写 |
| **Token** | 计量单位，见 §3.2 |
| **API / API Key** | 全大写；Key 大写 K，中间有空格 |
| **Markdown / GFM / KaTeX / HTML** | 原形 |
| **FAQ** | 全大写 |
| **DM** | 缩写保留；首现展开 direct message (DM) |
| **KNOWE_\*** | 环境变量前缀，全大写，含通配符 `*` |
| **SKILL.md** | 技能包描述文件名，原样 |
| **Windows / NSIS / Chromium / Playwright / DeepSeek** | 平台、安装器、运行时、模型提供方 |
| **¥** | 货币符号；首现可注 RMB (¥) |
| **v1.0.x** | 版本字符串，小写 v |
| **`dm:project:member`** | 群内私聊通道名，小写原样 |
| **⌘K** | 快捷键符号 |
| **PDF / Word / Excel / PPT** | 文件格式名 |

> 反向约束：**知知、花名册、审批卡、工作区、沙箱、派活、验收、交接、归档、脱敏** 等中文词一律不得出现在 EN 正文——即使 CN 正文里写了中文括号注，EN 侧只保留英文。

### 3.2 大小写约定

1. **专有名词**：Knowe、Zinnia、Markdown、GFM、KaTeX、HTML、FAQ、API、Token、DM、SKILL.md 按 §3.1 原形，不随句子位置变化（句首除外，句首仍大写首字母）。
2. **角色名 vs 泛指**：作为产品角色名时大写（the Coordinator proposed… / a Worker started working）；作泛指名词时小写（project manager / team member）。
3. **UI 元素名**：指具体界面元素时 Title Case（the Approval Card、the Task card）；泛指时小写（an approval card）。**同一页内保持统一**，推荐全文用「具体时 Title Case」规则。
4. **Token**：指代「Token 这个概念/界面术语」时恒为 Token（Token usage、Tokens in this conversation）；作纯计量单位且出现在正文句中时可小写复数 tokens——**若与产品 UI 显示冲突，以产品为准（产品面板中为 Token）**。
5. **文件名与 H1**：文件名用 PascalCase（§4.1）；H1 用 Title Case（§4.3）。
6. **状态词**：成员状态 Idle / Working / On standby / Archived；消息三态 Pending / Delivered / Uncertain；连接六态 Connecting / Connected / Reconnecting / Disconnected 等，均首字母大写。
7. **句内小写原则**：除上述规则外，普通英文句子遵循常规大小写，不为了「醒目」而大写普通词。

### 3.3 单复数约定

1. **可数名词正常变复数**：approval card → approval cards；skill pack → skill packs；knowledge asset → knowledge assets；workspace → workspaces；member → members；project → projects；contact → contacts；favorite → favorites（收藏条目）。
2. **不可数名词不变**：knowledge（永远没有 knowledges）；usage（Token usage）；context 作「上下文」概念时不可数，仅当指多个会话/项目的具体上下文时才用 contexts；memory 作「记忆」概念时不可数，指「一段记忆/多条记忆」时用 a memory / memories（项目记忆三层里的具体条目可用复数）。
3. **roster**：单数形式即表集合（the roster shows who is busy）；仅当描述多个项目的花名册时才用 rosters。
4. **data**：集合名词，文档统一用 data is（现代用法），全文保持一致，勿混用 data are。
5. **度量与数量**：中文量词（个/张/条/份）不翻译；数量表达直接写数字 + 英文复数（5 seconds、24 roles、6 tabs）。
6. **UI 文案**：产品界面中已定型的复数形式（如 Favorites、Contacts）作为视图名保留原样，不再按语法复变化。

### 3.4 高频误译 / 易踩坑清单

| 中文 | ❌ 勿译成 | ✅ 应译成 |
|:--|:--|:--|
| 花名册 | flower roster / name list | roster |
| 知知 | Knowledge / Kiki / Zhizhi | Zinnia |
| 派活 | dispatch work | assign a task |
| 群内私聊 | group inner private chat | in-project DM |
| 私聊不隐秘 | private chat is not secretive | DMs are not private |
| 审批卡 | audit card / review card | approval card |
| 脱敏 | desensitization | masking / redaction |
| 验收 | check and accept | acceptance / verify the deliverable |
| 交接 | hand over（动词化） | handoff |
| 沙箱 | sand box | sandbox |
| 用量 | consumption（成本语境勿用） | usage |
| 收藏 | collection / bookmark | favorite(s) |
| 工作区 | work area | workspace |
| 归档 | file away | archive |
| 停止 | halt / pause | stop |
| 归档而不是删除 | archive instead of delete | archive, not delete |
| 群聊 | group chatroom | group chat |
| 主/辅助模型 | main model / vice model | primary model / fallback model |

---

## 4. EN 文件命名方案

### 4.1 命名规则（硬约束）

1. **目录镜像**：EN 页面一律放 `docs/EN/`，目录结构与 `docs/CN/` 一一对应（当前 35 页）。
2. **保留编号前缀**：两位数章节号（00–80）+ 两位数页序（01–05），与 CN 文件完全一致；编号只用于排版与排序，不进入 URL（见 4.4）。
3. **语义段英文 PascalCase**：中文语义部分替换为英文，每个单词首字母大写，单词间用连字符 `-`；**省略虚词**（a / an / the / of 等），`and` 保留（如 `Assign-and-Accept`）。
4. **纯 ASCII**：仅允许 A–Z、a–z、0–9、连字符；**不得出现**中文、空格、下划线、括号、点号（扩展名 `.md` 除外）。
5. **一一对应**：CN 侧增页、改名、删页时，EN 侧同步镜像，编号与语义同步变化。
6. **长度**：文件名（含编号与 `.md`）≤ 45 字符，保证跨平台与 git 兼容。
7. **H1 与文件名解耦**：文件名省略虚词，但页内 H1 用自然英文（可含冠词），见 §4.3。

### 4.2 35 页对照表

> 列说明：**CN 文件**（现状）→ **EN 文件名**（本方案，硬约束）→ **规划 slug**（来自规划 §3，URL 用，仅参考）→ **EN H1 建议**（章节前缀 + Title Case 页面名）。

| # | CN 文件（docs/CN/） | EN 文件名（docs/EN/） | 规划 slug | EN H1 建议 |
|:--|:--|:--|:--|:--|
| 1 | 00-概述.md | **00-Overview.md** | overview | `# 00 Overview` |
| 2 | 01-快速开始.md | **01-Quickstart.md** | quickstart | `# 01 Quickstart` |
| 3 | 02-安装与系统要求.md | **02-Installation-and-System-Requirements.md** | installation | `# 02 Installation and System Requirements` |
| 4 | 10-01-用聊天指挥一支AI团队.md | **10-01-Lead-a-Team-by-Chat.md** | chat-team | `# 10 Core Concepts · Lead an AI Team by Chat` |
| 5 | 10-02-知知-项目经理-成员.md | **10-02-Zinnia-Coordinator-and-Workers.md** | agents-overview | `# 10 Core Concepts · Zinnia, the Coordinator, and Workers` |
| 6 | 10-03-项目与工作区.md | **10-03-Projects-and-Workspaces.md** | projects-workspace | `# 10 Core Concepts · Projects and Workspaces` |
| 7 | 10-04-审批机制.md | **10-04-Approval-Mechanism.md** | approvals | `# 10 Core Concepts · Approval Mechanism: The Boundary Between People and AI` |
| 8 | 10-05-记忆与上下文.md | **10-05-Memory-and-Context.md** | memory-context | `# 10 Core Concepts · Memory and Context` |
| 9 | 20-01-创建项目与组建团队.md | **20-01-Create-Project-and-Build-Team.md** | create-project | `# 20 Guides · Create a Project and Build a Team` |
| 10 | 20-02-管理团队.md | **20-02-Manage-Team.md** | manage-roster | `# 20 Guides · Manage the Team` |
| 11 | 20-03-派活与验收.md | **20-03-Assign-and-Accept.md** | assign-and-verify | `# 20 Guides · Assign and Accept` |
| 12 | 20-04-群聊与私聊.md | **20-04-Group-Chat-and-DM.md** | chat-and-dm | `# 20 Guides · Group Chat and DMs` |
| 13 | 20-05-文件与附件.md | **20-05-Files-and-Attachments.md** | files-attachments | `# 20 Guides · Files and Attachments` |
| 14 | 20-06-知识库与技能包.md | **20-06-Knowledge-Base-and-Skill-Packs.md** | knowledge-base | `# 20 Guides · Knowledge Base and Skill Packs` |
| 15 | 20-07-搜索收藏与联系人.md | **20-07-Search-Favorites-and-Contacts.md** | search-and-organize | `# 20 Guides · Search, Favorites, and Contacts` |
| 16 | 20-08-文件预览窗口.md | **20-08-File-Preview-Window.md** | file-preview | `# 20 Guides · File Preview Window` |
| 17 | 20-09-Token用量与费用.md | **20-09-Token-Usage-and-Cost.md** | token-usage | `# 20 Guides · Token Usage and Cost` |
| 18 | 30-01-模型与提供方.md | **30-01-Models-and-Providers.md** | models-providers | `# 30 Configuration · Models and Providers` |
| 19 | 30-02-审批通知与托盘.md | **30-02-Approvals-Notifications-and-Tray.md** | notifications | `# 30 Configuration · Approvals, Notifications, and the Tray` |
| 20 | 30-03-外观与界面语言.md | **30-03-Appearance-and-Interface-Language.md** | appearance-language | `# 30 Configuration · Appearance and Interface Language` |
| 21 | 30-04-账户与身份.md | **30-04-Account-and-Identity.md** | account-identity | `# 30 Configuration · Account and Identity` |
| 22 | 40-01-知识策展.md | **40-01-Knowledge-Curation.md** | knowledge-curation | `# 40 Advanced · Knowledge Curation` |
| 23 | 40-02-技能包管理.md | **40-02-Skill-Pack-Management.md** | skill-packs | `# 40 Advanced · Skill Pack Management` |
| 24 | 40-03-私聊记忆与权限边界.md | **40-03-DM-Memory-and-Permission-Boundaries.md** | dm-privacy | `# 40 Advanced · DMs, Memory, and Permission Boundaries` |
| 25 | 40-04-环境变量与部署形态.md | **40-04-Environment-Variables-and-Deployment.md** | environment-variables | `# 40 Advanced · Environment Variables and Deployment Modes` |
| 26 | 50-01-角色目录.md | **50-01-Roles-Catalog.md** | roles-catalog | `# 50 Reference · Roles Catalog` |
| 27 | 50-02-Markdown与公式渲染.md | **50-02-Markdown-and-Formula-Rendering.md** | markdown-rendering | `# 50 Reference · Markdown and Formula Rendering` |
| 28 | 50-03-术语表.md | **50-03-Glossary.md** | glossary | `# 50 Reference · Glossary` |
| 29 | 50-04-常见问题FAQ.md | **50-04-FAQ.md** | faq | `# 50 Reference · FAQ` |
| 30 | 60-01-连接与后端故障.md | **60-01-Connection-and-Backend-Issues.md** | connection-backend | `# 60 Troubleshooting · Connection and Backend Issues` |
| 31 | 60-02-模型与运行故障.md | **60-02-Model-and-Runtime-Issues.md** | model-runtime | `# 60 Troubleshooting · Model and Runtime Issues` |
| 32 | 60-03-目录与数据恢复.md | **60-03-Directory-and-Data-Recovery.md** | data-recovery | `# 60 Troubleshooting · Directory and Data Recovery` |
| 33 | 70-01-更新日志.md | **70-01-Changelog.md** | changelog | `# 70 Releases · Changelog` |
| 34 | 80-01-安全与隐私.md | **80-01-Security-and-Privacy.md** | security-privacy | `# 80 Support · Security and Privacy` |
| 35 | 80-02-联系我们.md | **80-02-Contact-Us.md** | contact | `# 80 Support · Contact Us` |

> 说明：`docs/CN/文档结构规划.md` 是规划稿，不属于 35 个发布页，不进 EN 目录树。
> 注：20-03 的规划 slug 为 assign-and-verify，本方案按任务要求将**文件名**定为 `Assign-and-Accept`（验收=acceptance）；slug 仅影响未来站点 URL，不影响本地文件名。

### 4.3 H1（页面主标题）命名规范

1. **与 CN 同构**：顶层页（00/01/02）H1 为 `# 00 Overview`（无章节名）；章节页 H1 为 `# 10 Core Concepts · 页面名`（与 CN「章节号 · 章节名 · 页面名」结构一致）。EN 章节名见 §2.6。
2. **Title Case**：主要单词首字母大写；冠词、≤3 字母的连词与介词小写（and / of / in / the）；**句首词例外，永远大写**（如 `... the Boundary Between People and AI` 中 the 在句首大写）。
3. **自然英文**：H1 可含冠词与完整表达（`Create a Project and Build a Team`），不必迁就文件名省略虚词。
4. **专有名词原形**：Token、Zinnia、Markdown、FAQ、API、DM、Knowe 按 §3.1 原样。
5. **长度**：H1 ≤ 60 字符（含章节前缀），避免换行。
6. **首现引用**：正文第一次提到页面名时用链接或斜体，写法与 H1 一致（如 *Assign and Accept*），避免同一页出现两种标题。
7. **页内标题层级**：H2/H3 用 Sentence case（仅首词与专有名词大写），与 H1 的 Title Case 区分（如 `## Sending a message`）。

### 4.4 本文件与正式术语表页的关系

- **本文件**（`docs/EN/glossary.md`）是写作期规范文档（等同 CN 的「文档结构规划」），不直接对外发布。
- **EN 正式术语表页**落点为 `docs/EN/50-03-Glossary.md`（对应 CN `50-03-术语表.md`），内容由本文件 §2 表格整理为可发布形式（中译英单向表 + 一句话定义 + 链接）。
- **本地文件名 vs URL**：本地文件保留编号前缀（本方案）；若未来部署站点，URL 使用规划 slug（`/docs/en/glossary` 等，规划 §3），两者由同一张对照表派生，互不冲突。

---

## 5. 术语入库与维护流程

1. **新词入库**：翻译中遇到本表未覆盖的词 → 先查产品 UI 英文文案 → 无果则按本文件规则给出译名 → 写入 §2 对应分组（含大小写、单复数、备注）。
2. **冲突处理**：产品内置英文字符串 > 本表 > 译者判断。发现本表与产品不一致时，以产品为准并回报更新本表。
3. **防漂移**：每批翻译前，对照产品 UI 英文导出复核一次高频词（角色、审批卡、花名册、Token 等），呼应规划 §6.3「术语表与角色目录建议做成数据驱动」——若后续引入数据源驱动角色表，本表同步切换为数据源口径。
4. **增删页联动**：CN 目录树变动时，§4.2 对照表同步更新；本文件是 EN 侧唯一命名权威。

---

*本文档为规范稿，不包含 EN 页面正文；35 个 EN 页面按 §2 术语与 §4 命名规范另行撰写。*
