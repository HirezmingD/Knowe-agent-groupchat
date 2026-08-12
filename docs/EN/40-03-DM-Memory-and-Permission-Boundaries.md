<!--
  Page: 40 Advanced · DMs, Memory, and Permission Boundaries
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 40 Advanced · DMs, Memory, and Permission Boundaries
  Status: published (ninth batch)
-->

# 40 Advanced · DMs, Memory, and Permission Boundaries

> **At a glance**: this page takes Knowe's privacy and permission boundaries apart: why **in-project DM content is written back to project memory** (and which DMs aren't), what the **three permission boundaries** (sandbox / memory / tools) each govern, and **what gets masked** (member internal ids, internal paths, API Keys). For the operational side (how to enter a DM, what the profile page looks like), see [20 Guides · Group Chat and DMs](20-04-Group-Chat-and-DM.md) and [20 Guides · Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md); for the mechanism side (why memory is designed this way), see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md).

**On this page**

- [Why DM content is written back to project memory](#why-dm-content-is-written-back-to-project-memory)
- [Which DMs are written back, which aren't](#which-dms-are-written-back-which-arent)
- [Where DM content sits in memory](#where-dm-content-sits-in-memory)
- [The three permission boundaries: sandbox, memory, and tools](#the-three-permission-boundaries-sandbox-memory-and-tools)
- [What gets masked](#what-gets-masked)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Why DM content is written back to project memory

In Knowe, you can double-click a member in the roster to open an **in-project direct message (DM)** (`dm:project:member`) and privately give that member extra background (entry: [20 Guides · Group Chat and DMs · In-project DMs: double-click a member in the roster](20-04-Group-Chat-and-DM.md#in-project-dms-double-click-a-member-in-the-roster)). **DMs are not private** — DM content is written back to the owning project's memory, and the Coordinator always knows what happens in DMs.

Why is it designed this way? Three reasons (details in [10 Core Concepts · Memory and Context · Why DM content is written back to the project](10-05-Memory-and-Context.md#why-dm-content-is-written-back-to-the-project)):

1. **A project is a whole** — a DM is private communication *within* the project, not a secret channel independent of it;
2. **Team collaboration needs complete information** — if the Coordinator doesn't know the background you give in a DM, its breakdown and verification become distorted;
3. **No "black-box communication"** — structurally rules out "reaching an agreement while bypassing the Coordinator".

So its positioning is clear: **it fits "don't want to spam the group chat but want the team to know" — not "don't want the Coordinator to know".**

## Which DMs are written back, which aren't

"The DM is written back to project memory" targets **in-project DMs**. Knowe has two kinds of DMs, with different owners:

| DM | Written back to project memory? | Notes |
|:--|:--|:--|
| **In-project DM** (`dm:project:member`, opened by double-clicking a member in the roster) | **Yes** | Part of the project context; the Coordinator always knows |
| **Platform DM with Zinnia** (the fixed window at the top of the conversation list on the left) | **No** | Belongs to no project — an independent reception channel (see [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)) |

In one sentence: **"DMs are not private" is about in-project DMs; the Platform DM with Zinnia is a different matter.** For the operational boundary explanation, see [20 Guides · Group Chat and DMs · "DMs are not private": boundary explanation](20-04-Group-Chat-and-DM.md#dms-are-not-private-boundary-explanation).

## Where DM content sits in memory

What's written back isn't a "DM notebook" — it's **project memory itself**. DM content is treated exactly like group chat messages and enters the three-layer structure:

| Layer | Where DM content sits |
|:--|:--|
| **Recent activity** | New DM messages enter recent activity and get loaded into context first |
| **Rolling summary** | As time moves on, older DM content is compressed into the rolling summary, keeping the global picture |
| **Historical activity segments** | Earlier DM records are archived for later tracing when needed |

(The full explanation of the three layers is in [10 Core Concepts · Memory and Context · The three layers of project memory](10-05-Memory-and-Context.md#the-three-layers-of-project-memory).) In other words, DM messages, like group chat messages, can be cited by later context and traced back — their place in memory is in no way "downgraded".

## The three permission boundaries: sandbox, memory, and tools

Every object (Zinnia / the Coordinator / Workers / groups) has a permission-boundary explanation on its profile page, organized along three dimensions (entry: [20 Guides · Search, Favorites, and Contacts · The contact profile page](20-07-Search-Favorites-and-Contacts.md#the-contact-profile-page)). This page expands each dimension:

| Dimension | What it governs | Where the boundary is |
|:--|:--|:--|
| **Sandbox** | **Which files** it can read and write | By default only inside the workspace directory — "can't get out" (see [10 Core Concepts · Projects and Workspaces · The workspace directory: the AI's sandbox](10-03-Projects-and-Workspaces.md#the-workspace-directory-the-ais-sandbox)); attachments you personally drag into the composer are the exception entry — and only paths "seen and signed" by the app are read (see [20 Guides · Files and Attachments · The safety guard: only files you picked yourself are read](20-05-Files-and-Attachments.md#the-safety-guard-only-files-you-picked-yourself-are-read)) |
| **Memory** | **Which context** it can see | Project memory (the three-layer structure) + the knowledge base (global / project scopes, see [20 Guides · Knowledge Base and Skill Packs · Two scopes: global and project](20-06-Knowledge-Base-and-Skill-Packs.md#two-scopes-global-and-project)); DM content is part of project memory |
| **Tools** | **Which capabilities** it can call | The member's toolbox — identical for every member (**same tools, different minds**, see [10 Core Concepts · Zinnia, the Coordinator, and Workers · Same tools, different minds](10-02-Zinnia-Coordinator-and-Workers.md#same-tools-different-minds)) |

Why does the profile page carry this? Because in Knowe "what can be done" has clear boundaries — you can look it up any time instead of guessing ([20 Guides · Search, Favorites, and Contacts · Permission boundaries: sandbox, memory, and tools](20-07-Search-Favorites-and-Contacts.md#permission-boundaries-sandbox-memory-and-tools)).

## What gets masked

Masking handles **internal identifiers** — what you see is readable information instead of machine identifiers:

- **Member internal ids** — always masked in the interface's natural language: you see the member's name, not the internal identifier;
- **Internal paths** — the same rule: the interface shows readable paths, internal paths are not directly exposed;
- **API Keys** — kept on this machine only, never written to browser storage (see [30 Configuration · Models and Providers · API Key security: not written to disk](30-01-Models-and-Providers.md#api-key-security-not-written-to-disk)); screens involving Keys follow the mask / leave-blank convention.

On the engineering side too, the renderer process doesn't directly hold Node capabilities (contextIsolation) — the UI layer can't bypass the app's boundaries to operate directly on local resources. That's another boundary of Knowe's privacy design.

> **Boundary**: the complete mechanism (what the memory projection includes, the full list of masking rules) is in [80 Support · Security and Privacy](80-01-Security-and-Privacy.md).

## Common questions

**Q: If DMs are written back to memory anyway, what's the point of DMs?**
There is one. It fits "don't want to spam the group chat but want the team to know" — privately giving a member extra background or handing over a piece of context; just don't use it as a secret channel.

**Q: Will the Platform DM with Zinnia be written back to a project?**
No. Zinnia's Platform DM belongs to no project — it's an independent reception channel (see [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)).

**Q: Which files on my machine can a member actually touch?**
By default only the workspace directory; attachments you personally drag into the composer are the exception entry, and only app-confirmed paths are read ([20 Guides · Files and Attachments · The safety guard: only files you picked yourself are read](20-05-Files-and-Attachments.md#the-safety-guard-only-files-you-picked-yourself-are-read)).

**Q: Will masking redact my chat content too?**
No. Masking targets internal identifiers like member internal ids and internal paths — not your chat content; chat content is stored and used per the project memory rules.

## Next steps

- Want to understand environment variables and deployment modes? → [40 Advanced · Environment Variables and Deployment Modes](40-04-Environment-Variables-and-Deployment.md)
- Want to review the operational side of "DMs are not private"? → [20 Guides · Group Chat and DMs · "DMs are not private": boundary explanation](20-04-Group-Chat-and-DM.md#dms-are-not-private-boundary-explanation)
- Want to review the three layers of memory? → [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)
- Where the data lives and the full masking mechanism → [80 Support · Security and Privacy](80-01-Security-and-Privacy.md)

---

**Previous**: [40-02 Skill Pack Management](40-02-Skill-Pack-Management.md)
**Next**: [40-04 Environment Variables and Deployment Modes](40-04-Environment-Variables-and-Deployment.md)
