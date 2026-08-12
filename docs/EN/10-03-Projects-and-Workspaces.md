<!--
  Page: 10 Core Concepts · Projects and Workspaces
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 10 Core Concepts · Projects and Workspaces
  Status: published (third batch)
-->

# 10 Core Concepts · Projects and Workspaces

> **At a glance**: in Knowe, a project = a group chat + a local workspace directory. This page explains why the "container" is designed this way: the directory is the single boundary where the AI reads and writes files (the sandbox — "it can't get out"), and it's also the landing spot for deliverables that you can open anytime and fully control. It also covers how the project name and the directory are bound to each other, and how to recover when the directory fails (details in [60 Troubleshooting · Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)).

**On this page**

- [A project: a group chat and a local directory](#a-project-a-group-chat-and-a-local-directory)
- [The workspace directory: the AI's sandbox](#the-workspace-directory-the-ais-sandbox)
- [What happens (and doesn't) in the directory](#what-happens-and-doesnt-in-the-directory)
- [Project name and directory binding](#project-name-and-directory-binding)
- [Recovery when the directory fails](#recovery-when-the-directory-fails)
- [What's inside a project](#whats-inside-a-project)
- [Next steps](#next-steps)

---

## A project: a group chat and a local directory

Knowe's smallest unit of work is the **Project**. A project consists of two parts, and both are required:

> **Project = group chat (the place for communication and decisions) + local workspace directory (the range where the AI reads and writes files)**

- **Group chat**: you, the Coordinator, and the Workers communicate in the same chat stream. Requirements are stated here, Approval Cards pop up here, and deliverables are reported here.
- **Workspace directory**: a **local folder** you pick yourself when creating the project (usually an empty directory). Every file the team produces lands here, and the AI can only read and write files inside it.

The two are bound together: the group chat is the project's "face", and the directory is the project's "body".

![](docs/assets/S15-项目群聊与工作区目录的对照（心智示意）.png)

Why a "directory" instead of letting the AI store deliverables in the cloud? Three reasons:

1. **The deliverables belong to you** — the files sit on your disk; you can open, back up, and take them away at any time, not locked inside some service;
2. **Clear boundaries** — the AI's working range is physically confined to one directory; it can't touch anything else on your machine;
3. **Freedom to move** — the directory is a local path; change computers or reinstall the system and the files are still there (see [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention)).

## The workspace directory: the AI's sandbox

"Sandbox" is the key word for understanding the workspace directory: **this is the single boundary where the AI reads and writes files — it can't get out.**

- The team (the Coordinator verifying, the Workers executing) reads and writes files **only inside this directory**;
- Any file outside the directory is, by default, unreadable and unwritable for the AI;
- Attachments you drag into the composer yourself are the one exception — and only paths the app has "seen and signed" with its own eyes are read, so it can't be tricked into reading arbitrary files (see [20 Guides · Files and Attachments](20-05-Files-and-Attachments.md)).

In one sentence: **whatever land you fence in, the team works within that land.** This is the first layer of Knowe's security boundary, and the most direct expression of "the range where the AI works is yours to control".

## What happens (and doesn't) in the directory

| Happens | Doesn't happen |
|:--|:--|
| Workers create and modify files here (like `index.html`, `style.css`) | The AI reads or writes any file outside the directory |
| The Coordinator reads the deliverables here when verifying | The AI deletes the whole directory or data outside it |
| You open the directory directly anytime and edit the files inside with any tool | Project data only lives in the cloud, away from your machine (chat history and other project data are in local `data/`, see [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)) |

## Project name and directory binding

When you create a project (the Create Project Approval Card), you set two things at once:

- **Project name** — the group chat's name, pre-filled with Zinnia's suggestion, changeable directly. It affects the interface display, not the file location.
- **Workspace directory** — required. It's where the project really "lands on disk".

The binding: **project name → project record → directory path**. Renaming only changes the name, not the directory; but once the directory is moved or renamed (for example, you move the folder in File Explorer), the project can no longer find it — that's the **Missing directory** state.

> **Tip**: the project name can be changed at any time (it changes the group chat's name); the directory path is fixed at creation. Switching the directory later belongs to the "Missing directory" recovery scenario, see below.

## Recovery when the directory fails

If the project directory is moved, renamed, or deleted, Knowe pops up a **Recovery card** in the chat stream (with a 5-minute countdown), giving you two choices:

- **Rename it** — update the project record to the directory's new path (for when the directory was moved/renamed);
- **Pick a new directory** — give the project a different workspace directory (for when the directory is really gone).

If the Recovery card's countdown ends without action, the card expires, but you can still start a recovery yourself (the specific steps are in [60 Troubleshooting · Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)). **Deleting the directory ≠ deleting the project** — the project record and chat history are still in your local data; the project just can't find its file landing spot for now.

## What's inside a project

Think of the project as a container: besides the group chat messages, it carries a whole set of assets that travel with the project:

| Content | What it is | Where to look |
|:--|:--|:--|
| Group chat messages and approval records | The complete process of requirements, decisions, confirms and rejects | [Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md) |
| Project memory | Recent activity, rolling summary, historical activity segments | [Memory and Context](10-05-Memory-and-Context.md) |
| Team roster | Current members, statuses, archive records | [Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md) |
| Workspace files | The real files the team produced | This page |
| Project knowledge | Reusable assets distilled from the project | [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md) |

When the project ends or is no longer needed, you can archive the team and keep the directory at any time — the project's data always stays on your machine, at your disposal.

## Next steps

- Want to understand why adding people and assigning tasks both need approval? → [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
- Want to understand how project memory is kept across turns? → [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)
- Want to create a project yourself? → [01 Quickstart · Steps 1–2](01-Quickstart.md#step-1--have-zinnia-create-your-project); the expanded step-by-step version is in [20 Guides · Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md)
- Directory having issues? → [60 Troubleshooting · Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)

---

**Previous**: [10-02 Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)
**Next**: [10-04 Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
