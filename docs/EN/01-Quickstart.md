<!--
  Page: 01 Quickstart
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 01 Quickstart
  Status: published (first batch)
-->

# 01 Quickstart

> **At a glance**: this page walks you through Knowe's complete main loop in 10 minutes — **create a project → build a team → assign a task → receive the deliverable**. Every step has concrete actions and interface explanations; at the end, a copy-ready sample conversation and a "you've made it" checklist: if these things appear on your screen, you're up and running.

**On this page**

- [Before you begin](#before-you-begin)
- [Step 1 · Have Zinnia create your project](#step-1--have-zinnia-create-your-project)
- [Step 2 · Confirm the Create Project card: project name and workspace directory](#step-2--confirm-the-create-project-card-project-name-and-workspace-directory)
- [Step 3 · Confirm the Build Team card: assemble the team](#step-3--confirm-the-build-team-card-assemble-the-team)
- [Step 4 · Assign a task and confirm the Task card](#step-4--assign-a-task-and-confirm-the-task-card)
- [Step 5 · Acceptance: receive the deliverable](#step-5--acceptance-receive-the-deliverable)
- [Sample conversation (copy-ready)](#sample-conversation-copy-ready)
- [You've made it](#youve-made-it)
- [Next steps](#next-steps)

---

## Before you begin

This page assumes you've **installed Knowe and completed the first-run model setup** — when you open it, you can see the main interface with Zinnia's DM window on the left.

- Not installed yet? Go to [02 Installation and System Requirements](02-Installation-and-System-Requirements.md) first — about 5 minutes.
- Stuck at the first-run model setup (the "pick a provider / enter your API Key / connection test" step)? See [02 Installation and System Requirements · First launch: model configuration guide](02-Installation-and-System-Requirements.md#first-launch-model-configuration-guide).

You only need three things before you start:

| What to prepare | Notes |
|:--|:--|
| An **empty directory** | Used as the project workspace (sandbox). Every file the team produces lands here, and the AI can only read and write files in this directory |
| A **concrete request** | The more specific, the faster the first run-through. For example: "Build a landing page for a company website — one page, plain HTML/CSS/JS" |
| About 10 minutes | You only click Confirm in 3 places the whole way (create project, build team, assign task) |

> **Tip**: if you state your request in the group chat but nothing happens, first check whether the connection status badge at the top of the window says **Connected**; if not, click **Retry** to restart the backend. See [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md).

## Step 1 · Have Zinnia create your project

When you open Knowe, you'll see Zinnia's DM window. She's the host between you and the platform: she helps you talk through what you want to do, then creates the project. Once the project is created, the Coordinator takes over, and Zinnia no longer gets involved in project matters.

In Zinnia's input box, just state your request:

> **You (to Zinnia)**
> I want to build a landing page for a company website — a single page is enough, plain HTML/CSS/JS, no frameworks. The product is a note-taking app called "Jianji", built around local storage and privacy first.

Zinnia will first confirm a few things with you (what to build, who it's for, roughly what you need). Once things are clear enough, she'll propose creating a project and pop up a **Create Project** Approval Card. **You don't need to type a reply now — go straight to Step 2.**

![](docs/assets/S02-知知私聊窗口与创建项目提议.png)

## Step 2 · Confirm the Create Project card: project name and workspace directory

A **Create Project** Approval Card pops up in the center of the screen — this is your first time exercising "the final say". The card has two key elements:

- **Project name** — pre-filled with Zinnia's suggestion; you can change it directly (short, specific, human-sounding — for example, "Jianji website landing page");
- **Workspace directory (required)** — click the "Choose directory" button and pick an **empty directory** (for example `D:\work\jianji-site`). The note on the card explains that the team can only read and write files in this directory — "it can't get out".

Click **Confirm**, and the project is created. The card status changes to **Approved**.

![](docs/assets/S03-创建项目审批卡.png)

**How you know this step is done**: your project group chat appears in the conversation list on the left (the project name next to the avatar), and the first message in the group chat is the Coordinator's entry note; Zinnia's DM window shows a note like "the project is created — the Coordinator will take it from here".

## Step 3 · Confirm the Build Team card: assemble the team

Once the Coordinator is in, it first reads the project and figures out which roles the request needs, then **proposes adding members** — a **Build Team** Approval Card pops up listing the candidate members: each person's avatar, name, role, and a one-line "Good at" note.

For a landing page, for example, the Coordinator usually proposes a lean team like this:

| Member | Role | Good at |
|:--|:--|:--|
| 林知远 | Frontend | Page and component implementation, interactions and styling, debugging in the browser |
| 苏禾 | UI/UX design | Information architecture, interaction flows, visual guidelines, design specs |

> **Tip**: choosing people is about role judgment, not the toolbox — every team member has the same tools; the difference is "who should think this through". Roles can be added anytime, so you don't need to fill the team on the first try.

When everything looks right, click **Confirm**. The Build Team card status changes to **Approved**, and the two members officially join the team.

![](docs/assets/S04-组建团队审批卡.png)

**How you know this step is done**: two members appear in the **member roster** on the right, with a status dot next to each name (**Idle**); the top of the roster shows "Members · 2".

## Step 4 · Assign a task and confirm the Task card

Once the team is in place, state your request clearly. **Talk in the project group chat, not to Zinnia** — project matters are handled by the Coordinator and the Workers.

In the project group chat's input box, tell the Coordinator directly what to do (an `@Coordinator` makes the message more prominent):

> **You (to the project group chat)**
> @Coordinator let's get started. Build a "Jianji" website landing page: one page, plain HTML/CSS/JS, no frameworks. Acceptance criteria: opening index.html directly in a browser shows three sections — the hero, the features, and pricing; the copy centers on "local storage, privacy first".

The Coordinator breaks down the request, then **assigns one task at a time**, popping up a **Task card** titled "Assign a task". The card usually contains:

- **Who it's assigned to** — the target member's name and role;
- **What to do** — the task instruction (specific about the goal and acceptance criteria);
- **The Coordinator's note** — additional guidance for the member, for example "follow 苏禾's visual approach";
- **Feedback history during approval** — if you want changes, you can leave feedback right on the card, and the Coordinator will pop it up again after revising.

Click **Confirm** when ready. The Task card status changes to **Approved**, and the member starts working.

![](docs/assets/S05-派发任务任务卡.png)

**How you know this step is done**: after the Task card is confirmed, that member's status in the roster changes to **Working**, and the member's work process appears in the group chat — **streaming output** appears word by word, possibly with work stage hints like "reading the request → writing index.html → writing style.css", plus the reasoning panel (the member's thinking process).

## Step 5 · Acceptance: receive the deliverable

When a Worker finishes, it submits a **report**, and the produced files appear in the chat stream as **File cards**; then the Coordinator **verifies in person** — reading the actual files in your workspace and checking that they match the task goal — and only then reports back to you.

At this point you can:

1. Click a **File card** in the chat stream to view it in a separate preview window (HTML/images/PDF/Word/code and more are supported);
2. Open the **workspace directory** you chose and see the files the Workers produced directly (like `index.html`, `style.css`);
3. Keep giving feedback in the group chat — for example, "make the hero copy shorter" — and the Coordinator will bring the revision back and assign the task again.

![](docs/assets/S06-成员报告 + 文件卡片 + 项目经理验收汇报.png)

**How you know this step is done**: the Coordinator clearly tells you "done" and points to where the deliverables are — not just "the Worker submitted a report".

## Sample conversation (copy-ready)

Below is a complete conversation script from creating a project to the first delivery. **Lines marked "(you act)" need your input**; the rest are the expected replies from the AI inside the product — send them as-is to run through, and they'll also give you a feel for how the team talks.

**Part 1 · Create the project with Zinnia**

> **You (to Zinnia)**
> I want to build a landing page for a company website — a single page is enough, plain HTML/CSS/JS, no frameworks. The product is a note-taking app called "Jianji", built around local storage and privacy first.
>
> **Zinnia**
> Sure — this kind of file-producing work is best done in a project, because the team there can write files directly and run them for you. Let me create a project for you first: we can call it "Jianji website landing page". Remember to pick a workspace directory on the card — all the files the team produces will live there.

**(you act)** On the Create Project card: confirm the project name → click "Choose directory" to pick an empty directory → click **Confirm**.

> **Coordinator**
> The project is ready. I'll build a lean team based on the request: Frontend writes the page, UI/UX design sets the visuals. Once both are confirmed, I'll bring them in.

**(you act)** On the Build Team card: click **Confirm** (林知远 · Frontend; 苏禾 · UI/UX design).

**Part 2 · Assign the task in the project group chat**

> **You (to the project group chat)**
> @Coordinator let's get started. Build a "Jianji" website landing page: one page, plain HTML/CSS/JS, no frameworks. Acceptance criteria: opening index.html directly in a browser shows three sections — the hero, the features, and pricing; the copy centers on "local storage, privacy first".
>
> **Coordinator**
> Got it. First task: 林知远 builds the page structure and styling, 苏禾 produces the visual approach. We verify each one as it's done.

**(you act)** On the Task card: click **Confirm**.

> **林知远 (Frontend) is working…** (streaming output: reading the request → writing index.html → writing style.css → local preview)
>
> **Coordinator** (after verifying the files)
> 林知远's first version is in place: `index.html` and `style.css` are both in the project directory — open them in a browser and you'll see the three-section structure. 苏禾's visual approach is still in progress; once it's in, I'll give you the full summary.

**Part 3 · Acceptance and next round**

> **苏禾 (UI/UX design) is working…** (streaming output: setting the visual guidelines → producing visual-guide.md)
>
> **Coordinator** (after verifying)
> Both have delivered. Current results in the project directory: `index.html`, `style.css`, and `visual-guide.md` (the visual guidelines). Click the File cards in the chat to preview, or open `index.html` directly to see the result. For next steps, should I have a tester go over the basic interactions, or would you rather look at the page first?

> **You (to the project group chat)**
> I've looked at the page — make the hero copy a bit shorter, leave everything else alone.
>
> **Coordinator**
> Got it. I'll take this feedback back, have 林知远 revise the hero copy, and deliver again.

> **Note**: this is an example that mirrors the product's real interactions — Zinnia creates the project, the Coordinator breaks things down and assigns tasks (one at a time, starting only after confirmation), the Workers stream their work and submit reports, and the Coordinator verifies the files before reporting. Actual wording may vary a bit with the model and project content, but the flow and the card shapes stay the same.

## You've made it

If **all** of the following show up on your screen, congratulations — you've made it through Knowe's core loop:

- [ ] Your **project group chat** is in the conversation list on the left (not just Zinnia's DM);
- [ ] The **roster** on the right has at least 2 members, and they've gone from **Idle** to **Working**;
- [ ] You clicked **Confirm 3 times** yourself (the Create Project card, the Build Team card, the Task card), and every Approval Card shows **Approved**;
- [ ] A member's **streaming work process** appeared in the chat stream (word-by-word output / work stage hints);
- [ ] **Files produced by the Workers** appeared in the workspace directory you chose;
- [ ] The Coordinator **explicitly reported completion** and suggested next steps;
- [ ] You can click the **File cards** in the chat stream to preview the results.

If any of these are missing, check against this table:

| Missing | Cause and where to go |
|:--|:--|
| No project group chat | The Create Project card wasn't confirmed successfully, or no directory was picked. Go back to [Step 2](#step-2--confirm-the-create-project-card-project-name-and-workspace-directory) |
| No members in the roster | The Build Team card wasn't confirmed. Go back to [Step 3](#step-3--confirm-the-build-team-card-assemble-the-team) |
| Nothing moves after you state the request | Check whether the connection badge says **Connected**; did you confirm the Task card? Go back to [Step 4](#step-4--assign-a-task-and-confirm-the-task-card) |
| No files in the directory | Check whether the workspace directory you picked is the project directory; see [60 Troubleshooting · Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md) |

## Next steps

- Want to understand design questions like "why approvals" and "why are DMs not private"? → 10 Core Concepts: [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md) is the master key, while [Approval Mechanism](10-04-Approval-Mechanism.md) and [Memory and Context](10-05-Memory-and-Context.md) explain the design rationale.
- Want to use the main loop in more detail? → 20 Guides has 9 pages published — look them up by task: [Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md), [Manage the Team](20-02-Manage-Team.md), [Assign and Accept](20-03-Assign-and-Accept.md), [Group Chat and DMs](20-04-Group-Chat-and-DM.md), [Files and Attachments](20-05-Files-and-Attachments.md), [Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md), [Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md), [File Preview Window](20-08-File-Preview-Window.md), [Token Usage and Cost](20-09-Token-Usage-and-Cost.md).
- Haven't installed Knowe yet, or want to know about uninstalling and data retention? → [02 Installation and System Requirements](02-Installation-and-System-Requirements.md).

---

**Previous**: [00 Overview](00-Overview.md)
**Next**: [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)
