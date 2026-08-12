<!--
  Page: 20 Guides · Knowledge Base and Skill Packs
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Knowledge Base and Skill Packs
  Status: published (fifth batch)
-->

# 20 Guides · Knowledge Base and Skill Packs

> **At a glance**: making the team smarter the more you use it — that's what the knowledge base is for: experience distilled while the AI works is settled into reusable **knowledge assets**, so the next similar project can reference them directly instead of stepping on the same trap again. This page covers: how the five types of knowledge assets appear as the four interface labels (conventions / pitfalls / patterns / checklists), the two scopes of global and project, what's on a knowledge card (times cited, sources, evidence list, citation trail), the asset lifecycle (Active / Pending review / Retired) and the operations you can do (approve, reject, rename, change scope, retire, delete), the three kinds of skill packs, and how the team actually consumes knowledge. The division of labor between memory and knowledge (memory = process, knowledge = distilled) is in [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md); this page is about the operational side.

**On this page**

- [Knowledge assets: five types and four labels](#knowledge-assets-five-types-and-four-labels)
- [Two scopes: global and project](#two-scopes-global-and-project)
- [The knowledge card: citations, sources, and evidence](#the-knowledge-card-citations-sources-and-evidence)
- [Lifecycle: Active, Pending review, Retired](#lifecycle-active-pending-review-retired)
- [Skill packs: three types, each with its own place](#skill-packs-three-types-each-with-its-own-place)
- [How the team consumes knowledge](#how-the-team-consumes-knowledge)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Knowledge assets: five types and four labels

Every entry in the knowledge base is a **knowledge asset** — reusable experience distilled while the AI works. By content, assets fall into five types:

| Asset type | What it is |
|:--|:--|
| **Preference** | How you (or the project) prefer to do things |
| **Practice** | Reusable steps and methods |
| **Pitfall** | Traps stepped on before — don't step on them again |
| **Fact** | Objective facts and agreed-upon definitions |
| **Decision** | Things decided and the reasoning behind them |

In the interface, these assets are organized with **four labels**: **conventions / pitfalls / patterns / checklists**. The two aren't two separate systems but two sides of the same thing — the five types say "where the knowledge comes from", and the four labels say "how it's filed in the interface". The typical mapping is roughly:

| Asset type (content) | Interface label | Example |
|:--|:--|:--|
| Preference | Convention | "Weekly report in A4 layout, PDF and md with the same name" |
| Practice | Convention / Pattern | "Have Data analysis double-check the numbers before acceptance" |
| Pitfall | Pitfall | "A known bug in some version of a framework" |
| Fact | Checklist | "Production environment addresses and ports" |
| Decision | Checklist | "The decision on this month's launch scope" |

> **Tip**: don't obsess over a one-to-one mapping — a piece of knowledge goes into the label that fits its content best; the exact filing is up to the content and your curation.

![](docs/assets/S30-知识库界面——四类标签与知识卡列表.png)

## Two scopes: global and project

Knowledge assets come in **two scopes**:

| Scope | Who can use it | What fits |
|:--|:--|:--|
| **Global knowledge** | All projects | Preferences, practices, and common pitfalls that apply across projects |
| **Project knowledge** | Only the current project | Decisions, facts, and conventions specific to this project |

What choosing a scope means: global knowledge saves you from re-stating your preferences in every project; project knowledge keeps "this project's business" effective only in this project, without polluting others. The scope can be **adjusted** on the knowledge card (see the operation list in the [Lifecycle](#lifecycle-active-pending-review-retired) section).

## The knowledge card: citations, sources, and evidence

Each piece of knowledge appears as a **knowledge card**; besides the title and the type label, the card carries four key pieces of information:

| Card info | What question it answers |
|:--|:--|
| **Cited N times** | How many tasks / conversations have referenced this knowledge — the more it's used, the more trustworthy |
| **M sources** | From how many pieces of material it was distilled |
| **Evidence list** | The sources backing this knowledge (messages, files) — each one traceable to its origin |
| **Citation trail** | Which tasks / projects have used it |

The point of this information: **knowledge isn't made up on the fly** — any asset can answer "where it came from, who used it, whether it's reliable", which is also what sets it apart from ordinary chat history.

## Lifecycle: Active, Pending review, Retired

A knowledge asset has a lifecycle with three states:

| State | Meaning | Who decides |
|:--|:--|:--|
| **Pending review** | Just distilled from the project process, not yet in effect | Waiting for your stance |
| **Active** | Approved; the team can cite it | After you approve |
| **Retired** | No longer in effect; kept for history | You retire it proactively |

Operations you can do (or do after approving):

- **Approve / Reject** — state your stance on a pending asset: approve and it takes effect; reject and it doesn't enter the knowledge base;
- **Rename** — make the title clearer;
- **Change scope** — switch the scope between global and project;
- **Retire** — stop an asset from taking effect (recoverable);
- **Permanently delete** — irreversible; confirm before you do it.

> **Note**: retiring and restoring, permanently deleting, and drilling into evidence are advanced knowledge-curation operations — see [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md); this page only says "these operations exist".

## Skill packs: three types, each with its own place

Besides knowledge assets, a project also carries a type of capability set called **skill packs**, in three types:

| Type | What it is | Modifiable? |
|:--|:--|:--|
| **System-bundled skills** | Basic capabilities built into the app (carrying SKILL.md definitions) | Immutable |
| **Project-experience skills** | Exported from the project's core knowledge, settling with the project | Curatable |
| **Third-party skill packs** | External skills with their own install directory and their own lifecycle | Installable / uninstallable, with an independent lifecycle |

In one sentence: **system skills come with the product, project-experience skills are what you build up yourselves, and third-party skill packs are installed from outside**. Installing, uninstalling, and lifecycle management of skill packs are in [40 Advanced · Skill Pack Management](40-02-Skill-Pack-Management.md).

## How the team consumes knowledge

The knowledge base isn't "stored to look nice" — the team's consumption path is a closed loop:

1. **Cited while working** — when executing a task, members cite the knowledge assets relevant to the current task (preferences, practices, pitfalls…), and the citations are recorded in the card's "Cited N times";
2. **Distilled back** — new experience found while working is distilled into new pending assets, waiting for your approval;
3. **Reused across projects** — global knowledge applies to all projects: the next similar project cites it directly instead of stepping on the same trap (memory = process, knowledge = distilled, see [10 Core Concepts · Memory and Context · Knowledge base and memory: division of labor](10-05-Memory-and-Context.md#knowledge-base-and-memory-division-of-labor)).

So the knowledge base "understands you" better the more you use it: the more preferences and pitfalls you build up in it, the less the team goes down wrong paths and repeats explanations in later projects.

## Common questions

**Q: Is knowledge produced automatically?**
What's recorded automatically is **memory** (the process); **knowledge is distilled** — it needs to be refined from the project process and goes through curation: pending assets only take effect after you approve them (see [Lifecycle](#lifecycle-active-pending-review-retired)).

**Q: What exactly is the difference between the knowledge base and memory?**
Memory = process (who said what, did what, what the result was); knowledge = distilled (reusable preferences, practices, pitfalls, facts, decisions). A comparison table is in [10 Core Concepts · Memory and Context · Knowledge base and memory: division of labor](10-05-Memory-and-Context.md#knowledge-base-and-memory-division-of-labor).

**Q: Can deleted knowledge be recovered?**
Retired assets can be restored; **permanently deleted ones are irreversible** — always confirm before doing it. Curation details are in [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md).

**Q: Can project knowledge be promoted to global knowledge?**
Yes — **change the scope** on the knowledge card, switching it from "project" to "global".

**Q: What if a skill pack breaks or I want to replace it?**
System skills are immutable; project-experience skills are curatable; third-party skill packs can be uninstalled and reinstalled, following their own lifecycle — see [40 Advanced · Skill Pack Management](40-02-Skill-Pack-Management.md).

## Next steps

- Want to master curating pending assets (approve / reject / retire / delete)? → [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md)
- Want to manage the three types of skill packs? → [40 Advanced · Skill Pack Management](40-02-Skill-Pack-Management.md)
- Want to understand the boundary between memory and knowledge? → [10 Core Concepts · Memory and Context · Knowledge base and memory: division of labor](10-05-Memory-and-Context.md#knowledge-base-and-memory-division-of-labor)
- Want to quickly find a piece of knowledge or a message in a project? → [20 Guides · Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md)

---

**Previous**: [20-05 Files and Attachments](20-05-Files-and-Attachments.md)
**Next**: [20-07 Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md)
