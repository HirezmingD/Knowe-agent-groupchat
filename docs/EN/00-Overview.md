<!--
  Page: 00 Overview
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 00 Overview
  Status: published (first batch)
-->

# 00 Overview

> **At a glance**: Knowe is a Windows desktop app — you lead an AI team by chat to get things done. You only need to say what you want; the AI Coordinator breaks down tasks, builds the team, assigns work, and verifies deliverables. Anything that involves people and work goes through an Approval Card in your hands first. This page answers three questions — what it is, what problems it solves, and who it's for — and closes with a map of the whole documentation set.

**On this page**

- [What Knowe is](#what-knowe-is)
- [What problems it solves](#what-problems-it-solves)
- [Mental model: leading an AI team by chat](#mental-model-leading-an-ai-team-by-chat)
- [Who it's for](#who-its-for)
- [Documentation map and reading paths](#documentation-map-and-reading-paths)
- [Next steps](#next-steps)

---

## What Knowe is

Knowe is a Windows desktop app. In one sentence:

> **Lead an AI team by chat to get things done.**

It's not another "AI assistant in a chat box" — it's a team. You create a **Project**: a group chat with a local workspace directory. Each project has one AI **Coordinator** and a number of AI **Workers**. You state your needs the way you'd send a message in a group chat; the Coordinator breaks the needs into tasks, brings people into the team, and assigns the work; the Workers carry it out. When they're done, the Coordinator verifies the deliverables and reports back to you.

Throughout the whole process, every action that involves people and work — creating a project, adding members, assigning tasks, removing members — is never executed silently. Instead, it pops up in front of you as an **Approval Card**, and you click **Confirm** or **Reject**. **You always have the final say; the AI can only propose.**

> For a full walkthrough of this mental model, see [10 Core Concepts · Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md).

## What problems it solves

A single AI assistant is great for chatting, but when you really want to "get something done", you often run into three obstacles:

| Pain point | Single AI assistant chat | Knowe's team form |
|:--|:--|:--|
| **The gap between idea and delivery** | After a round of chat, the conclusion stays in the chat box — nobody actually writes files, runs commands, or produces anything | Workers have real tools: they write project files, run commands, open a browser, and check facts online — the output lands in the workspace directory you chose |
| **Broken context** | Every new conversation starts from scratch explaining the background, and long chats outgrow the context | Projects have memory: recent activity, rolling summaries, and distilled knowledge follow the project, so the team stays consistent across turns |
| **Invisible process, untrustworthy results** | You don't know what it did or why, so you can only trust it (or not) by feel | The Workers' work is visible throughout (streaming output, reasoning, work stages), and the Coordinator **reads the files to verify** rather than trusting reports — only then does it report back to you |

In one sentence: Knowe takes the busywork between "idea" and "deliverable" — breaking down, building the team, executing, verifying, and distilling knowledge — off your plate, while keeping every key decision in your hands.

## Mental model: leading an AI team by chat

This picture is the master key to understanding everything Knowe can do:

![](docs/assets/S01-Knowe 主界面全景——「一图看懂」.png)

The three kinds of roles in the picture each do one thing:

| Role | Who | What they do | Boundaries |
|:--|:--|:--|:--|
| **You** | The user at the screen | State needs, make decisions | The final say; every team action waits for your approval |
| **Zinnia** | Platform-level host; there's only one across the whole platform | Helps you talk through what you want to do; creates projects, answers questions | Read-only, never enters the project; once the project is created, it's passed to the Coordinator |
| **Coordinator** | One general manager per project | Reads the project, breaks down tasks, proposes adding members / assigning / removing, verifies deliverables | **Can only propose, never decide**; doesn't write files directly |
| **Worker** | 24 roles, can be added or removed | Carries out the specific tasks assigned by the Coordinator, submits reports | Each has two boundaries: "Good at" and "Not suitable for" |

Three key mechanisms keep this running — understand these, and everything else is an extension:

- **Approval Card** — any add, assign, remove, or project creation waits for your confirmation as a card. The card has a countdown (it's withdrawn automatically when it times out) and four final states: Approved / Rejected / Timed out / Canceled. See [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md).
- **Project workspace** — every project is bound to a local directory (the sandbox); the AI can only read and write files inside this directory — it can't get out. See [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md).
- **Member roster** — the panel on the right shows in real time who is busy, who is idle, and who is archived; you can **Stop** a Worker who is working with one click.

There's one key idea behind choosing people: **same tools, different minds.** Every Worker in the team has exactly the same toolbox; the difference is professional judgment — assigning work is about "who should think this through", not "whose tools can do it".

## Who it's for

| Reader | Scenario | What you get |
|:--|:--|:--|
| **New users (no experience)** | Just got the installer, haven't configured any model | Gets it installed, gets it running, sees the first team working within 10 minutes |
| **Everyday users (the main audience)** | Know how to use it but not deeply; open it every few days to have the team handle chores by chat | The full routine of creating projects, adding members, assigning tasks, and accepting work; how to feed files to the AI; how knowledge gets distilled |
| **Advanced / heavy users** | Multiple projects in parallel, rely on distilled knowledge, care about token costs | How memory and context work, knowledge curation, per-member model binding, usage and cost |
| **Managers / decision-makers** | Evaluating whether "letting an AI team do the work" is reliable and where the safety boundaries are | The permission and approval mechanism, privacy and security boundaries, where the data lives |

It's also worth saying what it's **not for**: if you just want a one-off quick Q&A, Knowe can do that too (Zinnia can), but its design strengths are **ongoing work that needs deliverables and iteration** — writing pages, doing research, fixing bugs, producing documentation. A "look up a word for me" doesn't need a project.

## Documentation map and reading paths

The whole documentation set is organized as "get started first, then understand, then go deeper" — 8 groups, about 35 pages. The tree below matches the planning draft; all pages are published.

**Published pages (jump straight in)**

- [00 Overview](00-Overview.md) — this page
- [01 Quickstart](01-Quickstart.md)
- [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)
- 10 Core Concepts (second batch):
  - [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md)
  - [Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)
  - [Projects and Workspaces](10-03-Projects-and-Workspaces.md)
  - [Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
  - [Memory and Context](10-05-Memory-and-Context.md)
- 20 Guides · Main loop, 4 pages (third batch):
  - [Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md)
  - [Manage the Team](20-02-Manage-Team.md)
  - [Assign and Accept](20-03-Assign-and-Accept.md)
  - [Group Chat and DMs](20-04-Group-Chat-and-DM.md)
- 20 Guides · Mid-frequency, 5 pages (fourth batch):
  - [Files and Attachments](20-05-Files-and-Attachments.md)
  - [Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md)
  - [Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md)
  - [File Preview Window](20-08-File-Preview-Window.md)
  - [Token Usage and Cost](20-09-Token-Usage-and-Cost.md)
- 30 Configuration (fifth batch):
  - [Models and Providers](30-01-Models-and-Providers.md)
  - [Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md)
  - [Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md)
  - [Account and Identity](30-04-Account-and-Identity.md)
- 40 Advanced (sixth batch):
  - [Knowledge Curation](40-01-Knowledge-Curation.md)
  - [Skill Pack Management](40-02-Skill-Pack-Management.md)
  - [DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md)
  - [Environment Variables and Deployment Modes](40-04-Environment-Variables-and-Deployment.md)
- 50 Reference (seventh batch):
  - [Roles Catalog (24 roles)](50-01-Roles-Catalog.md)
  - [Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md)
  - [Glossary](50-03-Glossary.md)
  - [FAQ](50-04-FAQ.md)
- 60 Troubleshooting (eighth batch):
  - [Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md)
  - [Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md)
  - [Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)
- 70 Releases (tenth batch):
  - [Changelog](70-01-Changelog.md)
- 80 Support (ninth batch):
  - [Security and Privacy](80-01-Security-and-Privacy.md)
  - [Contact Us](80-02-Contact-Us.md)

**Complete documentation tree** (all pages published)

- 00 Overview — this page · published
- 01 Quickstart — published
- 02 Installation and System Requirements — published
- 10 Core Concepts
  - [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md) — published
  - [Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md) — published
  - [Projects and Workspaces](10-03-Projects-and-Workspaces.md) — published
  - [Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md) — published
  - [Memory and Context](10-05-Memory-and-Context.md) — published
- 20 Guides (task-oriented)
  - [Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md) — published
  - [Manage the Team](20-02-Manage-Team.md) — published
  - [Assign and Accept](20-03-Assign-and-Accept.md) — published
  - [Group Chat and DMs](20-04-Group-Chat-and-DM.md) — published
  - [Files and Attachments](20-05-Files-and-Attachments.md) — published
  - [Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md) — published
  - [Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md) — published
  - [File Preview Window](20-08-File-Preview-Window.md) — published
  - [Token Usage and Cost](20-09-Token-Usage-and-Cost.md) — published
- 30 Configuration
  - [Models and Providers](30-01-Models-and-Providers.md) — published
  - [Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md) — published
  - [Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md) — published
  - [Account and Identity](30-04-Account-and-Identity.md) — published
- 40 Advanced
  - [Knowledge Curation](40-01-Knowledge-Curation.md) — published
  - [Skill Pack Management](40-02-Skill-Pack-Management.md) — published
  - [DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md) — published
  - [Environment Variables and Deployment Modes](40-04-Environment-Variables-and-Deployment.md) — published
- 50 Reference
  - [Roles Catalog (24 roles)](50-01-Roles-Catalog.md) — published
  - [Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md) — published
  - [Glossary](50-03-Glossary.md) — published
  - [FAQ](50-04-FAQ.md) — published
- 60 Troubleshooting
  - [Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md) — published
  - [Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md) — published
  - [Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md) — published
- 70 Releases
  - [Changelog](70-01-Changelog.md) — published
- 80 Support
  - [Security and Privacy](80-01-Security-and-Privacy.md) — published
  - [Contact Us](80-02-Contact-Us.md) — published

Pick the shortest path for your profile:

- **New users**: [Quickstart](01-Quickstart.md) → [Installation and System Requirements](02-Installation-and-System-Requirements.md). Get it running first, then fill in the background.
- **Everyday users**: start with the [Quickstart](01-Quickstart.md) to reinforce the main loop; for questions like "why do approvals exist" and "why are DMs not private", enter 10 Core Concepts from [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md); 20 Guides has 9 pages published (main loop: [Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md), [Manage the Team](20-02-Manage-Team.md), [Assign and Accept](20-03-Assign-and-Accept.md), [Group Chat and DMs](20-04-Group-Chat-and-DM.md); mid-frequency: [Files and Attachments](20-05-Files-and-Attachments.md), [Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md), [Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md), [File Preview Window](20-08-File-Preview-Window.md), [Token Usage and Cost](20-09-Token-Usage-and-Cost.md)) — look them up by your task as needed; 30 Configuration has 4 pages published ([Models and Providers](30-01-Models-and-Providers.md), [Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md), [Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md), [Account and Identity](30-04-Account-and-Identity.md)) — consult them when tuning models, notifications, appearance, or your account; 40 Advanced has 4 pages published ([Knowledge Curation](40-01-Knowledge-Curation.md), [Skill Pack Management](40-02-Skill-Pack-Management.md), [DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md), [Environment Variables and Deployment Modes](40-04-Environment-Variables-and-Deployment.md)) — consult them when you want to go further; 50 Reference has 4 pages published ([Roles Catalog](50-01-Roles-Catalog.md), [Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md), [Glossary](50-03-Glossary.md), [FAQ](50-04-FAQ.md)) — consult them when picking roles, looking up terms, or checking rendering rules; 60 Troubleshooting has 3 pages published ([Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md), [Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md), [Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)) — consult them when you can't connect, get model errors, or hit a missing directory.
- **Advanced users**: 10 Core Concepts is published — you can start with [Memory and Context](10-05-Memory-and-Context.md); "Token Usage and Cost" is published — check [Usage and Cost](20-09-Token-Usage-and-Cost.md) first; 30 Configuration is published — consult [Models and Providers](30-01-Models-and-Providers.md) (per-member model binding, fallback model) and [Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md) as needed; 40 Advanced is published ([Knowledge Curation](40-01-Knowledge-Curation.md), [Skill Pack Management](40-02-Skill-Pack-Management.md), [DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md), [Environment Variables and Deployment Modes](40-04-Environment-Variables-and-Deployment.md)) — consult as needed; 50 Reference is published — [Glossary](50-03-Glossary.md) is the go-to entry point for consistent terminology ([Roles Catalog](50-01-Roles-Catalog.md), [Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md), [FAQ](50-04-FAQ.md) as needed); 60 Troubleshooting has 3 pages published ([Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md), [Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md), [Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)) — consult them for troubleshooting and data backup scenarios; 70 Releases is published — see [Changelog](70-01-Changelog.md) to confirm what changed in each version.
- **Managers**: [Approval Mechanism](10-04-Approval-Mechanism.md) is published — read it closely; "Security and Privacy" is published — see [Data and Permission Boundaries](80-01-Security-and-Privacy.md); you can also start with the mechanisms section in the [Overview](#mental-model-leading-an-ai-team-by-chat).

## Next steps

- Not installed yet? → [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)
- Already installed and want to run your first project in 10 minutes? → [01 Quickstart](01-Quickstart.md)

---

**Previous**: (none — this page is the start of the documentation)
**Next**: [01 Quickstart](01-Quickstart.md)
