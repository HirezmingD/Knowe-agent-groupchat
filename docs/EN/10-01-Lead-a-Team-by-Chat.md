<!--
  Page: 10 Core Concepts · Lead an AI Team by Chat
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 10 Core Concepts · Lead an AI Team by Chat
  Status: published (second batch)
-->

# 10 Core Concepts · Lead an AI Team by Chat

> **At a glance**: every piece of Knowe's design revolves around one mental model — you're not facing an "AI in a chat box"; you're leading an AI team coordinated by a Coordinator. This page explains the model in full: who does what in the team, why every key action passes through you, what "you have the final say; the AI can only propose" means, and how it differs in substance from a traditional AI assistant or a one-on-one conversation. It's the master key to every other page — the rest of the concept pages branch out from here.

**On this page**

- [You're not using a single AI — you're leading a team](#youre-not-using-a-single-ai--youre-leading-a-team)
- [Mental model: lead, not chat](#mental-model-lead-not-chat)
- [How it differs from a traditional AI assistant](#how-it-differs-from-a-traditional-ai-assistant)
- [You have the final say; the AI can only propose](#you-have-the-final-say-the-ai-can-only-propose)
- [What this model explains](#what-this-model-explains)
- [Next steps](#next-steps)

---

## You're not using a single AI — you're leading a team

The [Overview](00-Overview.md) already introduced what Knowe is: a Windows desktop app for leading an AI team by chat. This page unpacks that sentence and explains what "leading a team" really means.

The traditional way of using AI is one-on-one conversation: you open a window, throw a question at a single model, and get an answer back. Knowe isn't that shape. In Knowe, you're facing an **organization with division of labor, process, and boundaries** — you decide, the team executes, and the results land in your own directory. One diagram shows the roles and the decision flow:

```
                  You (the user) · the final say
                 ▲        │
  state needs ·  │        │  every action involving people
  accept results │        ▼  and work first becomes an
                 │           Approval Card, awaiting your call
                 │
  Zinnia (host) ──► Coordinator ──► Workers (24 roles) ──► workspace (sandbox)
  opens projects   breaks down ·    execute ·             deliverables land
  answers          schedules ·      submit reports        in your directory
  questions        verifies
```

This diagram is the foundation of the entire documentation set. Every later page — the roles, projects and workspaces, the approval mechanism, memory and context — answers one piece of "why" in this diagram. Three phrases matter most:

- **Lead, not do it for you** — you say what you want done; the team breaks down and executes the "how"; but **the power to decide always stays in your hands**.
- **Propose, not execute** — the Coordinator and the Workers can only propose and request; anything that involves people and work has to pass your approval first.
- **A team, not a single point** — breaking down, executing, and verifying are different people doing different jobs and checking each other, not one model talking to itself.

## Mental model: lead, not chat

To understand Knowe, set aside the habit of "chatting with an AI" for a moment and picture a different scene: you're in charge of a project, with a general manager (the Coordinator) and a professional team (the Workers) under you.

Your way of working isn't writing every file yourself. It is:

1. **State the goal clearly** — make "what it should look like" explicit;
2. **Make the key decisions** — take a stance on the plans the general manager puts forward: confirm, reject, or give revision feedback;
3. **Accept the results** — check whether the deliverables meet the standard you asked for, and send them back for another iteration if not.

The general manager's job: read your goal, figure out which people it needs, break the big goal into verifiable pieces of work, assign them, and after the Workers hand the results back, **personally verify** before reporting to you. The Workers' job: pick up the task and execute it, producing real files and results — not just a reply.

> **Tip**: this division of labor is written into Knowe's product behavior, not a verbal agreement. Workers have real tools (reading and writing project files, running commands, checking facts online), the Coordinator has a verifying duty (it reads files to verify and never takes reports at face value), and the Approval Cards in your hands are a hard boundary. See [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md).

## How it differs from a traditional AI assistant

Put Knowe side by side with a "single AI assistant / one-on-one conversation" and the differences are obvious at a glance:

| Dimension | Traditional AI assistant (one-on-one) | Knowe (AI team) |
|:--|:--|:--|
| **Who you talk to** | One model playing every role | Zinnia, the Coordinator, and the Workers each do their own job, each with their own "face" |
| **Who breaks down tasks** | You (or the model casually suggesting steps) | The Coordinator breaks tasks down as its dedicated job, into verifiable pieces of work |
| **Who executes** | The model "suggests you do it" | Workers really execute: they write files, run commands, and produce results that land in the workspace |
| **Who verifies results** | Usually no verification step — you trust (or not) by feel | The Coordinator reads the files to verify before reporting to you |
| **Who decides** | The model often "decides for you" or hedges | Every action involving people and work passes an Approval Card — you call the shots |
| **Context** | Every new conversation starts from zero; long chats forget | Projects have memory and distilled knowledge; consistent across turns |
| **Extensibility** | The capability ceiling of one model | 24 roles, added or removed as needed; team size follows the project |

Cross-reference the pain-point table in the [Overview](00-Overview.md) (the gap between idea and delivery, broken context, invisible process) — these seven rows are exactly how Knowe breaks each one down.

## You have the final say; the AI can only propose

This is Knowe's most important collaboration paradigm, and it deserves its own section.

- **The AI can only propose** — the Coordinator can propose adding members, assigning tasks, or removing members, and Zinnia can propose creating a project, but **any proposal is just a card**; nothing takes effect automatically.
- **You have the final say** — every card waits for your stance: confirm, reject, or simply leave it to time out. Until you nod, the team stays put.

Why is it designed this way? Two reasons:

1. **"People and work" is where cost and risk live** — adding a person means ongoing consumption of your model calls and tokens, and traces left in the project; assigning a task means a member uses tools and reads and writes files. These actions shouldn't be done silently by the AI on your behalf.
2. **Trust is built on visibility** — you've personally seen and confirmed every key action; the team's behavior is always in your sight, never acting on its own inside a black box.

> **Going further**: the concrete form of approval — the four kinds of Approval Cards, the countdown, and the four final states — is in [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md). Where this paradigm's boundary falls, and which actions need approval, are both on that page.

## What this model explains

Read Knowe's interface and features through the "leading a team" mental model, and a lot of the design clicks into place:

| Feature you see | Which piece of the mental model it maps to |
|:--|:--|
| Project group chat | The chat form of "one project = one team + one workspace", see [Projects and Workspaces](10-03-Projects-and-Workspaces.md) |
| Approval Cards | The mechanism that makes "you have the final say; the AI can only propose" real |
| The member roster | The real-time visibility of the "team": who's busy, who's idle, who's archived |
| Project memory and knowledge base | The "team" doesn't forget across turns: the process is remembered, the experience is distilled, see [Memory and Context](10-05-Memory-and-Context.md) |
| Role division (Zinnia / Coordinator / Workers) | "Lead, not chat" made concrete, see [Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md) |

If you remember only one sentence from this page, make it this: **Knowe isn't for chatting with an AI — it's for leading an AI team. You decide; the team executes; every key decision stays in your hands.**

## Next steps

- Want to meet everyone on the team? → [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)
- Want to understand "project = group chat + workspace"? → [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)
- Want to understand why every action goes through approval? → [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
- Haven't installed Knowe yet? → [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)

---

**Previous**: [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)
**Next**: [10-02 Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)
