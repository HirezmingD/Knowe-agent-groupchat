<!--
  Page: 10 Core Concepts · Memory and Context
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 10 Core Concepts · Memory and Context
  Status: published (third batch)
-->

# 10 Core Concepts · Memory and Context

> **At a glance**: a traditional AI assistant starts from zero in every new conversation; Knowe's projects are different — they remember. This page explains the three-layer structure of project memory (recent activity / rolling summary / historical activity segments), why DM content is also written back to the project (DMs are not private), the division of labor between the knowledge base and memory (memory = process, knowledge = distilled), and how context is kept across turns.

**On this page**

- [Why a project needs memory](#why-a-project-needs-memory)
- [The three layers of project memory](#the-three-layers-of-project-memory)
- [Why DM content is written back to the project](#why-dm-content-is-written-back-to-the-project)
- [Knowledge base and memory: division of labor](#knowledge-base-and-memory-division-of-labor)
- [How context is kept across turns](#how-context-is-kept-across-turns)
- [Next steps](#next-steps)

---

## Why a project needs memory

[Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md) mentioned that Knowe solves three pain points of traditional AI assistants, one of which is **broken context**: every new conversation requires re-explaining the background from scratch, and the model forgets once conversations get long.

Project memory is the design for exactly this pain point. A project can span days and dozens of conversations from creation to wrap-up; if it "lost its memory" every time you opened it, the team would always be beginners. Knowe's approach: **the project carries its own memory — everything that happens in the project becomes part of the project's context.**

Memory happens automatically; you don't need to manually "save". Sending messages, confirming Approval Cards, members working, submitting reports — all of it is written into project memory.

## The three layers of project memory

Project memory isn't one big basket; it's layered, with three layers each doing its own job:

```
              Project memory (travels with the project, kept across turns)
┌──────────────────────────────────────────────────────────────┐
│ Recent activity — newest messages and actions, fully kept    │ ← loaded into the context window first
├──────────────────────────────────────────────────────────────┤
│ Rolling summary — older activity compressed into a summary,  │ ← resident in context, keeps the big picture
│ continuously updated                                         │
├──────────────────────────────────────────────────────────────┤
│ Historical activity segments — earlier full records,         │ ← pulled out when needed
│ archived by segment, traceable                               │
└──────────────────────────────────────────────────────────────┘
```

| Layer | What it holds | What it does | When it's used |
|:--|:--|:--|:--|
| **Recent activity** | Recent messages, approval confirmations, members' work process | Precisely reconstructs "what's happening right now" | Every reply depends on it; loaded first |
| **Rolling summary** | Compressed summaries of older activity, continuously updated over time | Keeps a grasp on the project's overall progress; background isn't lost as content grows | Resident in context |
| **Historical activity segments** | Earlier full records, archived by time segment | The "original archive" for checking details | Read on demand when backtracking |

The key to understanding the three layers: **memory isn't "remember everything"; it's "layered access".** The newest, most relevant content is precisely kept; older content is compressed into summaries to preserve the big picture; even older content is archived and pulled out when needed. This way, replies aren't slowed down by memory growing without bound, and nothing is really lost.

## Why DM content is written back to the project

In Knowe, you can double-click a member in the roster to enter an **in-project DM** (`dm:project:member`) — for example, to privately give a member extra background, or to tell the Coordinator something that's awkward to spread in the group chat.

**But DMs are not private**: DM content is **written back to the owning project's memory** too, and the Coordinator always knows what happened in DMs.

Why is it designed this way? Three reasons:

1. **The project is a whole** — a DM is "in-project private communication", not a secret channel independent of the project. It belongs to the project's context just like the group chat;
2. **Team collaboration needs complete information** — if you give a member key background in a DM and the Coordinator doesn't know, its breakdown and verification would be distorted. Writing back to the project is how the general manager keeps the whole picture;
3. **No "black-box communication"** — DMs not being private structurally rules out "reaching agreements behind the Coordinator's back".

> **Boundary**: this rule targets **in-project DMs** (`dm:project:member`). Your Platform DM with Zinnia doesn't belong to any project — it's a separate channel with the platform host. More details on DMs, memory, and permission boundaries are in [40 Advanced · DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md), and the operational side of "DMs are not private" is in [20 Guides · Group Chat and DMs · "DMs are not private": boundary explanation](20-04-Group-Chat-and-DM.md#dms-are-not-private-boundary-explanation).

## Knowledge base and memory: division of labor

Memory and the knowledge base are easy to confuse; the division is actually clear:

> **Memory = process, knowledge = distilled.**

| Dimension | Project memory | Knowledge base |
|:--|:--|:--|
| Content | **Process**: who said what, did what, what the result was | **Distilled**: reusable preferences, practices, pitfalls, facts, decisions |
| How it's produced | Recorded automatically, no intervention needed | **Distilled** from the project process; needs curation (Pending review / approve / reject) |
| Lifecycle | Rolls with the project: new activity comes in, old activity is compressed into summaries and archived | Long-term valid; can be retired, deleted, and reused across projects |
| Who maintains it | The system, automatically | You curate it (or approve it) |
| Scope | The current project's context | Two scopes: "Global knowledge / Project knowledge" |

An example: a member finds "a known bug in some version of a framework" in the project — as a **process**, it goes into memory; if you approve distilling it into a **knowledge asset** (type "pitfall"), it enters the knowledge base, and next time a similar project can reference it directly without stepping on it again. See [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md).

## How context is kept across turns

String the sections above together and you get the complete chain of keeping context across turns:

1. **Write** — group chat messages, approval confirmations, members' work process, and DM content are all auto-written back to project memory;
2. **Layer** — recent activity precisely kept; older activity compressed into a rolling summary; even older content archived as historical activity segments;
3. **Read** — each time the Coordinator or a Worker replies, it first loads "recent activity + rolling summary" for the current context, and reaches back into history segments when details are needed;
4. **Distill** — the parts worth reusing long-term enter the knowledge base through your curation, becoming the team's capability rather than a passing piece of process.

So you can safely **come back days later and continue**: open the project group chat, say "continue from last time", and the team picks up — because the project's memory travels with the project, not with any single conversation.

> **Tip**: the granularity of memory and the privacy boundaries (what gets masked, what's in the memory projection) are in [80 Support · Security & Privacy](80-01-Security-and-Privacy.md).

## Next steps

- Want to understand the "memory" container itself? → [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)
- Want to understand how approval records become part of memory? → [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
- Want to sort out how knowledge assets are curated? → [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md)
- Want to review the whole mental model diagram? → [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md)

---

**Previous**: [10-04 Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
**Next**: [20-01 Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md)
