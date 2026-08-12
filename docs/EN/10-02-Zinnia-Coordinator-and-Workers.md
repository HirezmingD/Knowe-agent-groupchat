<!--
  Page: 10 Core Concepts · Zinnia, the Coordinator, and Workers
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 10 Core Concepts · Zinnia, the Coordinator, and Workers
  Status: published (second batch)
-->

# 10 Core Concepts · Zinnia, the Coordinator, and Workers

> **At a glance**: Knowe has three kinds of AI roles — Zinnia is the platform-level host, the Coordinator is the one general manager per project, and the Workers are 24 roles, addable and removable. This page makes clear who each of them is, what they do, and where their boundaries lie — and what the iron rule of picking people, "same tools, different minds", means: why assignment picks people by role, not by toolbox.

**On this page**

- [Three roles, three division lines](#three-roles-three-division-lines)
- [Zinnia: the platform-level host](#zinnia-the-platform-level-host)
- [Coordinator: one general manager per project](#coordinator-one-general-manager-per-project)
- [Workers: 24 roles, addable and removable](#workers-24-roles-addable-and-removable)
- [Same tools, different minds](#same-tools-different-minds)
- [Why assignment picks by role](#why-assignment-picks-by-role)
- [Next steps](#next-steps)

---

## Three roles, three division lines

Building on the mental model from [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md) — you decide, the team executes — the "people" in the team come in three kinds. Add yourself and that's four parties:

| Role | Who | What they do | Boundaries | When they appear |
|:--|:--|:--|:--|:--|
| **You** | The user at the screen | State needs, make decisions, accept results | The final say; every team action waits for you | The whole time |
| **Zinnia** | Platform-level host; only one across the whole platform | Helps you talk "what you want to do" into clarity; opens projects, answers questions | Read-only, never enters the project; once the project is created, it's handed to the Coordinator | Outside projects (DM) |
| **Coordinator** | One general manager per project | Reads the project, breaks down tasks, proposes adding members / assigning / removing, verifies deliverables | **Only the right to propose, never to decide**; doesn't write files directly | Inside each project |
| **Worker** | 24 roles, addable and removable | Executes the specific tasks as assigned, submits reports | Each has two boundaries: "Good at" and "Not suitable for" | After being brought into a project |

To sum up the relationship in one sentence: **Zinnia leads you through the door, the Coordinator runs things inside, and the Workers do the hands-on work.** The three don't sit on the same level, and none of them can take another's place.

## Zinnia: the platform-level host

Zinnia is the first AI you meet when you open Knowe — the fixed DM window in the conversation list on the left, and the only one of her kind across the whole platform.

- **What she does**: helps you turn a vague idea into an executable requirement; proposes opening a project (the Create Project Approval Card); answers everyday questions.
- **Boundary**: once the project is created, the Coordinator takes over — **Zinnia doesn't enter the project or take part in anything inside it**. Her role in a project stops at "opening the door".

Thinking of Zinnia as "the door" fits best: you enter the project through her; once inside, it's a different crew.

## Coordinator: one general manager per project

Every project has exactly one Coordinator — the general manager of that project. Its job is one complete loop:

> Reads the project background → judges which roles the request needs → proposes adding people (the Build Team card) → breaks the big goal into verifiable pieces of work → proposes assigning tasks (the Task card) → after the Workers hand the results back, **personally verifies** (reads the files to verify) → reports to you → keeps breaking down the next piece based on your feedback.

The key lies in two boundaries:

- **Only the right to propose, never to decide** — adding members, assigning tasks, and removing members can only be proposed in the form of Approval Cards, waiting for your confirmation. See [Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md).
- **Doesn't write files directly** — execution is the Workers' job. The Coordinator is the scheduler and the verifier, not the executor.

It's precisely because of this "propose–decide" boundary that you can safely hand "breaking down" to the Coordinator while keeping every key decision yourself.

## Workers: 24 roles, addable and removable

Workers are the ones who actually do the work — and the most flexible part of the team:

- **24 roles**: Frontend, Backend, Product, QA, UI/UX design, Data analysis, DevOps, Security, AI, Mobile, Game, GIS, Marketing, Finance, Healthcare, Education, Spatial computing, Technical support, Site reliability, Database, Architecture, Technical writing, Audio/Video, Legal — each role corresponds to one kind of professional judgment. (The full table of the 24 roles is in [50 Reference · Roles Catalog](50-01-Roles-Catalog.md).)
- **Addable and removable**: adding people is proposed by the Coordinator and confirmed by you (the Build Team card); removing people also goes through approval (the Remove a member card). A removed member enters the **Archived** state — the history bubbles stay, and you can invite the member back at any time.
- **Each has two identity details**: a **name** (unique within the project, avatar bound for life) and a **status** (Idle / Working / On standby / Archived), both visible in real time in the roster on the right.
- **Each has two boundaries**: "Good at" and "**Not suitable for**" — see below.

![](docs/assets/S14-成员资料页——擅长领域与权限边界.png)

## Same tools, different minds

This is the most important sentence in Knowe's people-picking logic, and it deserves its own section:

> **Every member of the team has exactly the same toolbox; the difference is professional judgment.**

Whether Frontend or Legal, what a member can do — read and write project files, run commands, check facts online, call models — is exactly the same. Tools don't differentiate. **What differs is "who should think this through"**:

- For the same marketing copy, a Technical writing member first structures the piece and aligns the information hierarchy; a Marketing member first thinks about the audience and the conversion goal;
- For the same requirement, a Product member first questions "whose problem does this feature solve", while a QA member first thinks about "which scenarios will break".

So "Good at" and "Not suitable for" are not a ranking of ability — **Good at** means "this kind of judgment is safest left to them", and **Not suitable for** means "this kind of judgment is likely to go off track with them". Both are division-of-labor suggestions, not verdicts on ability.

## Why assignment picks by role

Apply "same tools, different minds" to assignment, and you get Knowe's people-picking principle:

| Scenario | Who gets it | Why |
|:--|:--|:--|
| Building a page with interactions | A Frontend Worker | The judgment lives in browser behavior, state management, and styling details |
| Setting the information architecture and visual guidelines | A UI/UX design Worker | The judgment lives in user paths, hierarchy, and consistency |
| Troubleshooting an unavailable production service | A Site reliability / DevOps Worker | The judgment lives in the failure surface, the dependency chain, and the recovery order |
| Drafting a compliance statement | A Legal Worker | The judgment lives in wording risk and compliance boundaries |
| Producing a product research report | A Product / Data analysis Worker | The judgment lives in problem definition and evidence interpretation |

The reverse — the **Not suitable for** boundary — deserves the same respect: asking a Legal Worker to write frontend interactions, or a QA Worker to set the brand tone, isn't "they can't do it"; it's that the direction of judgment is likely wrong, and the rework costs more.

In practice you don't need to memorize every role's boundaries — the Coordinator **proposes** candidates based on the request first (both the Build Team card and the Task card carry a "Good at" note), and you confirm. When you want to pick people yourself, the roster and the member profile page are your basis for deciding.

## Next steps

- Want to understand the container of "project = group chat + workspace"? → [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)
- Want to understand why adding people needs approval? → [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
- Want to look up all 24 roles' "Good at" and "Not suitable for"? → [50 Reference · Roles Catalog](50-01-Roles-Catalog.md)
- Forgot the master key? → back to [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md)

---

**Previous**: [10-01 Lead a Team by Chat](10-01-Lead-a-Team-by-Chat.md)
**Next**: [10-03 Projects and Workspaces](10-03-Projects-and-Workspaces.md)
