<!--
  Page: 50 Reference · Glossary
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 50 Reference · Glossary
  Status: published (tenth batch)
-->

# 50 Reference · Glossary

> **At a glance**: this page is the first stop for aligning on site-wide terminology. Every term gets a one-line definition and links to the body page (with an anchor where one exists) — to dig deeper, follow the link; for a term the body pages don't make clear, come back here to align on wording. The definitions match the published pages exactly (for example "DMs are not private", "sandbox", "the five knowledge asset types"); exact UI copy follows the product.

**On this page**

- [Roles and the team](#roles-and-the-team)
- [Projects and workspaces](#projects-and-workspaces)
- [Approval and decisions](#approval-and-decisions)
- [Communication and messages](#communication-and-messages)
- [Knowledge and memory](#knowledge-and-memory)
- [Configuration, cost, and operations](#configuration-cost-and-operations)
- [Next steps](#next-steps)

---

## Roles and the team

| Term | One-line definition | See also |
|:--|:--|:--|
| **Zinnia** | The platform-level AI host with a fixed DM window: helps you talk through "what you want to do", proposes opening projects, answers everyday questions — and doesn't enter the project once it's built | [Zinnia: the platform-level host](10-02-Zinnia-Coordinator-and-Workers.md#zinnia-the-platform-level-host) |
| **Coordinator** | The one general manager per project: reads the project, breaks down tasks, proposes adding members / assigning work / removing members, verifies deliverables; **can only propose, you decide** | [Coordinator: one general manager per project](10-02-Zinnia-Coordinator-and-Workers.md#coordinator-one-general-manager-per-project) |
| **Worker** | One of the 24 roles; pulled into a project to execute concrete tasks and submit reports; each has a "Good at" and a "Not suitable for" boundary | [Workers: 24 roles, addable and removable](10-02-Zinnia-Coordinator-and-Workers.md#workers-24-roles-addable-and-removable) |
| **Roles Catalog** | The quick-reference table of the 24 worker roles (Good at / Not suitable for), matching the in-product Roles Catalog | [50 Reference · Roles Catalog](50-01-Roles-Catalog.md) |
| **Roster** | The project's live status panel for members: who's busy, who's idle, who's archived | [The roster: the team's live status](20-02-Manage-Team.md#the-roster-the-teams-live-status) |
| **Archive** | Moving a member out of the working lineup while keeping all history, so they can be invited back anytime; Knowe has no "delete member" | [Removing members: archive, not delete](20-02-Manage-Team.md#removing-members-archive-not-delete) |
| **Stop** | Interrupting a member who is working; inline double confirmation — auto-cancels in 5 seconds if you don't confirm | [Stop: interrupt a working member](20-02-Manage-Team.md#stop-interrupt-a-working-member) |

## Projects and workspaces

| Term | One-line definition | See also |
|:--|:--|:--|
| **Project** | Knowe's smallest unit of work: one group chat + one local workspace directory, both required | [A project: a group chat and a local directory](10-03-Projects-and-Workspaces.md#a-project-a-group-chat-and-a-local-directory) |
| **Workspace (directory)** | The local directory you choose when creating a project: every file the team produces lands here, and the AI can only read and write files here | [The workspace directory: the AI's sandbox](10-03-Projects-and-Workspaces.md#the-workspace-directory-the-ais-sandbox) |
| **Sandbox** | The everyday name for the workspace directory: the AI's only boundary for reading and writing files — "can't get out"; attachments are the only exception entry | [The workspace directory: the AI's sandbox](10-03-Projects-and-Workspaces.md#the-workspace-directory-the-ais-sandbox) |
| **Missing directory** | When the project directory is moved / renamed / deleted, a recovery card with a 5-minute countdown appears in the chat stream (rename it or pick a new directory) | [Recovery when the directory fails](10-03-Projects-and-Workspaces.md#recovery-when-the-directory-fails) |
| **Data directory** | `data/` (app data) and `Logs/` (logs) under the installation directory; involved in backup and uninstall | [The data directory: data and Logs](40-04-Environment-Variables-and-Deployment.md#the-data-directory-data-and-logs) |

## Approval and decisions

| Term | One-line definition | See also |
|:--|:--|:--|
| **Approval Card** | The mechanism where the four "people and work" actions — create project / add member / assign task / remove member — wait for your confirmation as cards | [The four kinds of Approval Cards](10-04-Approval-Mechanism.md#the-four-kinds-of-approval-cards) |
| **Create Project card** | The Approval Card that pops up when Zinnia proposes a project: project name + workspace directory (required) | [Step 2 · Confirm the Create Project card: project name and workspace directory](20-01-Create-Project-and-Build-Team.md#step-2--confirm-the-create-project-card-project-name-and-workspace-directory) |
| **Build Team card** | The Approval Card that pops up when the Coordinator proposes adding members: candidate member list (avatar, name, role, Good at) | [Step 4 · Confirm the Build Team card: build the team](20-01-Create-Project-and-Build-Team.md#step-4--confirm-the-build-team-card-build-the-team) |
| **Task card** | The Approval Card that pops up when the Coordinator proposes assigning a task: who it's assigned to + the task instruction + the Coordinator's note; you can give revision feedback while it's pending | [The Task card in detail: three fields](20-03-Assign-and-Accept.md#the-task-card-in-detail-three-fields) |
| **Four final states** | The four outcomes of an Approval Card: Approved / Rejected / Timed out / Canceled; the countdown follows the server-side clock | [Countdown and the four final states](10-04-Approval-Mechanism.md#countdown-and-the-four-final-states) |
| **Approval timeout** | The card auto-withdraws when the countdown finishes (≠ Rejected); the limit can be set to 5 / 10 / 30 / 60 / 180 / 300 seconds or no limit in Settings | [Changing the approval timeout](10-04-Approval-Mechanism.md#changing-the-approval-timeout) |

## Communication and messages

| Term | One-line definition | See also |
|:--|:--|:--|
| **In-project DM** | The private chat channel inside a project, opened by double-clicking a member in the roster (`dm:project:member`) | [In-project DMs: double-click a member in the roster](20-04-Group-Chat-and-DM.md#in-project-dms-double-click-a-member-in-the-roster) |
| **DMs are not private** | In-project DM content is written back to the project memory too, and the Coordinator always knows — a DM is not a secret channel | ["DMs are not private": boundary explanation](20-04-Group-Chat-and-DM.md#dms-are-not-private-boundary-explanation) |
| **Quote / Forward / Favorite** | Three message actions: Quote jumps back to the original; Forward keeps the original format and lets you add a comment; Favorites are collected into the Favorites view | [Message actions: Quote / Forward / Favorite / Copy](20-04-Group-Chat-and-DM.md#message-actions-quote--forward--favorite--copy) |
| **File card** | The entry to a file (attachment or member deliverable) in the chat stream; clicking it opens a separate preview window | [Opening a preview: click the File card](20-08-File-Preview-Window.md#opening-a-preview-click-the-file-card) |
| **Connection status badge** | The connection status indicator in the window (Connecting / Connected / Reconnecting / Disconnected, and others); click "Retry" when the backend is abnormal | [Ports and the backend: the local running mode](40-04-Environment-Variables-and-Deployment.md#ports-and-the-backend-the-local-running-mode) |

## Knowledge and memory

| Term | One-line definition | See also |
|:--|:--|:--|
| **Memory** | The project-level process record in three layers (recent activity / rolling summary / historical activity segments), automatic and kept across turns | [The three layers of project memory](10-05-Memory-and-Context.md#the-three-layers-of-project-memory) |
| **Knowledge asset** | Reusable experience distilled while the AI works, in five types (preferences / practices / pitfalls / facts / decisions); the interface organizes them with four tags (conventions / pitfalls / patterns / checklists) | [Knowledge assets: five types and four labels](20-06-Knowledge-Base-and-Skill-Packs.md#knowledge-assets-five-types-and-four-labels) |
| **Global knowledge / Project knowledge** | The two scopes of knowledge assets: global knowledge is available to every project; project knowledge only to the current project | [Two scopes: global and project](20-06-Knowledge-Base-and-Skill-Packs.md#two-scopes-global-and-project) |
| **Pending review / Active / Retired** | The three lifecycle states of knowledge assets: Pending review waits for your call, Active is citable by the team, Retired keeps the history | [Lifecycle: Active, Pending review, Retired](20-06-Knowledge-Base-and-Skill-Packs.md#lifecycle-active-pending-review-retired) |
| **Knowledge curation** | The advanced operations on assets under review: approve / reject, retire and restore, permanently delete, and deep-diving the evidence and citation trail | [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md) |
| **Skill pack** | The three kinds of packs that carry reusable capabilities: system-bundled (immutable), project-experience (curatable), third-party (independent lifecycle) | [Skill packs: three types, each with its own place](20-06-Knowledge-Base-and-Skill-Packs.md#skill-packs-three-types-each-with-its-own-place) |
| **Memory vs. knowledge** | Memory = process (who said what, who did what); knowledge = distillation (reusable preferences, practices, pitfalls, facts, decisions) | [Knowledge base and memory: division of labor](10-05-Memory-and-Context.md#knowledge-base-and-memory-division-of-labor) |

## Configuration, cost, and operations

| Term | One-line definition | See also |
|:--|:--|:--|
| **Primary model** | The model the whole team uses by default, bound forcibly at first launch; can be changed anytime later in Settings | [Primary model: binding and the connection test](30-01-Models-and-Providers.md#primary-model-binding-and-the-connection-test) |
| **Fallback model** | An optional backup model: once configured, the app automatically falls back to it when the primary model is unavailable | [The fallback model and automatic fallback](30-01-Models-and-Providers.md#the-fallback-model-and-automatic-fallback) |
| **Per-member model binding** | Assigning a specific model to individual members while the rest keep using the primary model | [Per-member model binding: when it's worth it](30-01-Models-and-Providers.md#per-member-model-binding-when-its-worth-it) |
| **API Key** | The access credential for a model provider: kept on this machine only, never written to browser storage, and can be cleared in Settings | [API Key security: not written to disk](30-01-Models-and-Providers.md#api-key-security-not-written-to-disk) |
| **Token usage** | The locally counted model-call dashboard: filter by date range → stat cards → trend chart → breakdown table | [The usage dashboard: how to read it from top to bottom](20-09-Token-Usage-and-Cost.md#the-usage-dashboard-how-to-read-it-from-top-to-bottom) |
| **Cost (¥)** | The model-call cost in RMB (¥) in the Token usage dashboard | [How cost is calculated](20-09-Token-Usage-and-Cost.md#how-cost-is-calculated) |
| **Environment variables (`KNOWE_*`)** | Backend switches you can change without opening the UI, organized by category (backend / terminal / network / browser / memory / skills) | [KNOWE_* environment variables: categories and uses](40-04-Environment-Variables-and-Deployment.md#knowe_-environment-variables-categories-and-uses) |
| **Masking** | Internal identifiers — member internal ids, internal paths — are masked in the interface's natural language so they're not exposed to you | [What gets masked](40-03-DM-Memory-and-Permission-Boundaries.md#what-gets-masked) |

## Next steps

- Want to pick people by role and see the 24-role table? → [50 Reference · Roles Catalog](50-01-Roles-Catalog.md)
- Want to know which Markdown syntax the chat stream supports? → [50 Reference · Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md)
- Hit a specific problem and want to locate it fast? → [50 Reference · FAQ](50-04-FAQ.md)

---

**Previous**: [50-02 Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md)
**Next**: [50-04 FAQ](50-04-FAQ.md)
