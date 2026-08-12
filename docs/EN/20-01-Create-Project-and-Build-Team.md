<!--
  Page: 20 Guides · Create a Project and Build a Team
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Create a Project and Build a Team
  Status: published (fourth batch)
-->

# 20 Guides · Create a Project and Build a Team

> **At a glance**: this page is the first step into Knowe's main loop. Following the steps here, you'll have a project: with a name, a workspace directory, a Coordinator, and the first batch of Workers. The whole way, you only click Confirm twice (the Create Project card and the Build Team card); Zinnia and the Coordinator drive the rest. This page only covers "how"; the design questions — what a project is, why a directory is required, why adding members needs approval — are covered in [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md) and [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md).

**On this page**

- [Before you begin](#before-you-begin)
- [Start with Zinnia: turn an idea into a requirement](#start-with-zinnia-turn-an-idea-into-a-requirement)
- [Step 1 · Have Zinnia propose a project](#step-1--have-zinnia-propose-a-project)
- [Step 2 · Confirm the Create Project card: project name and workspace directory](#step-2--confirm-the-create-project-card-project-name-and-workspace-directory)
- [Step 3 · The Coordinator moves in](#step-3--the-coordinator-moves-in)
- [Step 4 · Confirm the Build Team card: build the team](#step-4--confirm-the-build-team-card-build-the-team)
- [Step 5 · The roster: the team is in place](#step-5--the-roster-the-team-is-in-place)
- [Sample first-round conversation (copy-ready)](#sample-first-round-conversation-copy-ready)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Before you begin

This page assumes you've **installed Knowe and completed the first-run model setup** — when you open it, you can see Zinnia's DM window.

- Not installed yet? Go to [02 Installation and System Requirements](02-Installation-and-System-Requirements.md).
- Want to run through the whole thing in 10 minutes first and come back for details later? Go straight to [01 Quickstart](01-Quickstart.md) — this page is its "expanded version", with each step explained in more detail and ready to follow.

Two things to prepare before you start:

| What to prepare | Notes |
|:--|:--|
| An **empty directory** | Used as the project workspace (sandbox). Every file the team produces lands here, and the AI can only read and write files in this directory. An empty one is the least hassle the first time |
| A **concrete request** | What you want to do, who it's for, roughly what it should look like. The more specific, the easier for Zinnia and the Coordinator to get started |

> **Tip**: if you state your request in the group chat but nothing happens, first check whether the connection status badge at the top of the window says **Connected**; if not, click **Retry** to restart the backend. See [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md).

## Start with Zinnia: turn an idea into a requirement

The entry point for creating a project is **Zinnia's DM window** — the fixed window at the top of the conversation list on the left. Zinnia is the platform-level host; her job is to help you talk "what you want to do" into clarity, then propose opening a project. Once the project is created, the Coordinator takes over and Zinnia doesn't enter the project (for the division of roles, see [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)).

You don't need to organize a "professional requirements document" — just tell her in plain words what you have in mind. She may ask one or two follow-up questions (who it's for, what form it should take, roughly what you need); once things are clear enough, she'll propose creating a project.

> **Key point**: the goal of this step isn't to "finalize the plan" — it's to get Zinnia to the point where she thinks it's time to open a project. Details can keep being discussed inside the project — the Coordinator will help you break them down.

## Step 1 · Have Zinnia propose a project

In Zinnia's input box, state your request directly:

> **You (to Zinnia)**
> I want to turn the customer feedback scattered across places into one structured document: grouped by issue, with occurrence counts and impact noted, updated weekly if possible. The project can be called "Customer feedback summary".

Zinnia will first confirm a few things with you (where the feedback comes from, who the document is for, whether charts are wanted). Once things are clear enough, she'll reply "I'll create a project for you first" and pop up a **Create Project** Approval Card. **You don't need to type a reply now — go straight to Step 2.**

![](docs/assets/S02-知知私聊窗口与创建项目提议.png)

## Step 2 · Confirm the Create Project card: project name and workspace directory

A **Create Project** Approval Card pops up in the center of the screen — this is the first time you exercise the "final say": without your confirmation, the project won't be created. The card has two key elements:

- **Project name** — pre-filled with Zinnia's suggestion; you can change it directly. Keep it short, specific, and human-sounding — for example, "Customer feedback summary". It's just the group chat's name and can be changed anytime; it doesn't affect the file location (see [10 Core Concepts · Projects and Workspaces · Project name and directory binding](10-03-Projects-and-Workspaces.md#project-name-and-directory-binding)).
- **Workspace directory (required)** — click the "Choose directory" button and pick an **empty directory** (for example `D:\work\feedback-summary`). The note on the card explains that the team can only read and write files in this directory — "it can't get out".

Click **Confirm** when everything looks right, the card status changes to **Approved**, and the project is created.

![](docs/assets/S03-创建项目审批卡.png)

**How you know this step is done**: your project group chat appears in the conversation list on the left (the project name next to the avatar); Zinnia's DM window shows a note like "the project is created — the Coordinator will take it from here".

## Step 3 · The Coordinator moves in

Once the project is created, the **Coordinator** enters: its first message appears in the project group chat — usually a self-introduction and what it plans to do next. The Coordinator is the one general manager per project; it reads the project, breaks down the request, proposes adding members and assigning tasks, and verifies deliverables — **it only has the right to propose, never to decide** (see [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)).

It will first read the request you gave Zinnia, then judge: which roles does this project need, and should members be added first.

## Step 4 · Confirm the Build Team card: build the team

If the Coordinator judges that extra hands are needed, it will **propose adding members** — a **Build Team** Approval Card pops up, listing the candidate members: each person's avatar, name, role, and a one-line "Good at" note.

For "Customer feedback summary", for example, the Coordinator usually proposes a team like this:

| Member | Role | Good at |
|:--|:--|:--|
| 江澈 | Data analysis | Organizing data, statistical classification, finding patterns, making charts |
| 顾言 | Technical writing | Structuring documents, organizing information clearly, writing the copy |

> **Tip**: choosing people is about role judgment, not the toolbox — every team member has the same tools; the difference is "who should think this through" (see [10 Core Concepts · Zinnia, the Coordinator, and Workers · Same tools, different minds](10-02-Zinnia-Coordinator-and-Workers.md#same-tools-different-minds)). Roles can be added anytime, so you don't need to fill the team on the first try.

Click **Confirm** when everything looks right, the Build Team card status changes to **Approved**, and the two members officially join the team.

![](docs/assets/S04-组建团队审批卡.png)

**How you know this step is done**: two members appear in the **member roster** on the right, with a status dot next to each name; the top of the roster shows "Members · 2".

## Step 5 · The roster: the team is in place

Once the team is in place, take a look at the **roster** on the right: each member has one row — avatar, name, role tag, and status dot (members who just joined show **Idle**). This is the "team's live status" you can check at any time in the project — who's busy, who's idle, who's been archived, all in this panel (how to read it and how to manage the team, see [20 Guides · Manage the Team](20-02-Manage-Team.md)).

Now you can start assigning work: **talk in the project group chat, not to Zinnia** — matters inside the project are handled by the Coordinator and the Workers. Next step: [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md).

## Sample first-round conversation (copy-ready)

Below is a complete conversation script from creating a project to the team in place. **Lines marked "(you act)" need your input**; the rest are the expected replies from the AI inside the product — send them as-is to run through.

> **You (to Zinnia)**
> I want to turn the customer feedback scattered across places into one structured document: grouped by issue, with occurrence counts and impact noted, updated weekly if possible. The project can be called "Customer feedback summary".
>
> **Zinnia**
> Sure — this kind of ongoing work is best done in a project: the sorting process and the document will both land in the workspace directory you pick. Let me create a project for you first: we can call it "Customer feedback summary", and remember to pick a workspace directory on the card — the files the team produces will live there.

**(you act)** On the Create Project card: confirm the project name → click "Choose directory" to pick an empty directory → click **Confirm**.

> **Coordinator**
> The project is ready. For this request, I'll build the team first: Data analysis handles the sorting and classification, Technical writing organizes the information into a document. Once both are confirmed, I'll bring them in.

**(you act)** On the Build Team card: click **Confirm** (江澈 · Data analysis; 顾言 · Technical writing).

> **Coordinator**
> Both are in. Now give me more detail on the request: where does the feedback come from (email, surveys, customer-service records)? I'll break the tasks down by source and take them one at a time.

> **Note**: this is an example that mirrors the product's real interactions — Zinnia creates the project, the Coordinator builds the team and breaks things down, and you only click Confirm twice. Actual wording may vary a bit with the model and project content, but the flow and the card shapes stay the same.

## Common questions

**Q: Do I have to go through Zinnia first to create a project?**
The entry point for creating a project is Zinnia's DM: tell her your request, and she'll propose opening a project. Once the project is created, day-to-day operations all happen in the project group chat, and Zinnia no longer touches what's inside the project.

**Q: Can the project name be changed later?**
Yes. The project name is just the group chat's name and can be changed anytime; renaming doesn't affect the binding to the workspace directory (see [Projects and Workspaces · Project name and directory binding](10-03-Projects-and-Workspaces.md#project-name-and-directory-binding)).

**Q: Does the workspace directory have to be empty?**
Not required, but an empty directory is recommended the first time. The team can read — and may modify — files already in the directory; an empty one keeps "what the team produces" and "your own stuff" cleanly apart.

**Q: What if the team turns out too small and I need more people?**
You can add members anytime — have the Coordinator propose, and confirm one Build Team card. The specifics of adding, removing, and inviting members back are in [20 Guides · Manage the Team](20-02-Manage-Team.md).

**Q: Why does adding members need my confirmation instead of just pulling them in?**
Adding a member means that person starts continuously consuming your model calls and tokens and leaving traces in the project — this is a "people and work" action, and the decision stays with you. The design rationale is in [10 Core Concepts · Approval Mechanism · Why every team action needs approval](10-04-Approval-Mechanism.md#why-every-team-action-needs-approval).

## Next steps

- The team is in place — assign the first task → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- Want to manage the roster (add, remove, invite back, Stop)? → [20 Guides · Manage the Team](20-02-Manage-Team.md)
- Want to review why "project = group chat + workspace" is designed this way? → [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)
- Haven't run through the complete main loop yet? → [01 Quickstart](01-Quickstart.md)

---

**Previous**: [10-05 Memory and Context](10-05-Memory-and-Context.md)
**Next**: [20-02 Manage the Team](20-02-Manage-Team.md)
