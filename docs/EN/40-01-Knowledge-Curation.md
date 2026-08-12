<!--
  Page: 40 Advanced · Knowledge Curation
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 40 Advanced · Knowledge Curation
  Status: published (eighth batch)
-->

# 40 Advanced · Knowledge Curation

> **At a glance**: [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md) covered what operations knowledge assets have; this page goes through the full procedures and consequences of the advanced ones: **approving and rejecting** pending assets (and what happens after a rejection), **retiring and restoring** (reversible), **permanently deleting** (irreversible, and what to confirm before you do it), and the **citation trail and evidence deep-dive** (from a knowledge card back to the original messages, files, and tasks). It helps to first understand the asset lifecycle and what's on a knowledge card — reading [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md) first is recommended.

**On this page**

- [From "what operations" to "the full procedures"](#from-what-operations-to-the-full-procedures)
- [Lifecycle recap: three states](#lifecycle-recap-three-states)
- [Pending assets: approve and reject](#pending-assets-approve-and-reject)
- [Retire and restore](#retire-and-restore)
- [Permanent delete: irreversible](#permanent-delete-irreversible)
- [Citation trail and evidence deep-dive](#citation-trail-and-evidence-deep-dive)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## From "what operations" to "the full procedures"

In [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md), the lifecycle section lists the operations you can do (or do after approving): **approve / reject, rename, change scope, retire, permanently delete**. "Rename" and "change scope" are lightweight operations that take effect immediately — just work the knowledge card. This page expands on the **advanced** ones: they either have irreversible consequences (permanently delete), involve an asset's long-term state (retire and restore), or need you to follow the evidence chain all the way to the source (citation trail and evidence deep-dive).

> **Prerequisite**: the lifecycle model of knowledge assets and what's on a knowledge card follow [20 Guides · Knowledge Base and Skill Packs · Lifecycle: Active, Pending review, Retired](20-06-Knowledge-Base-and-Skill-Packs.md#lifecycle-active-pending-review-retired); this page only adds "how to do it and what the consequences are".

## Lifecycle recap: three states

First, a recap of the three states of a knowledge asset (see [20 Guides · Knowledge Base and Skill Packs · Lifecycle: Active, Pending review, Retired](20-06-Knowledge-Base-and-Skill-Packs.md#lifecycle-active-pending-review-retired)):

| State | Meaning | Who decides |
|:--|:--|:--|
| **Pending review** | Just distilled from the project process, not yet in effect | Waiting for your stance |
| **Active** | Approved; the team can cite it | After you approve |
| **Retired** | No longer in effect; kept for history | You retire it proactively |

The whole main line goes like this:

> **Pending review → you approve → Active → you retire → Retired → you restore → Active**; at any point, you can **permanently delete** the asset to make it disappear (irreversible).

## Pending assets: approve and reject

Pending assets are the "byproduct" of the team working — members distill new experience during work and produce pending assets waiting for your stance (see [20 Guides · Knowledge Base and Skill Packs · How the team consumes knowledge](20-06-Knowledge-Base-and-Skill-Packs.md#how-the-team-consumes-knowledge)). Your two possible stances have completely different consequences:

| Your action | Consequence | Team's perspective |
|:--|:--|:--|
| **Approve** | The asset enters "Active" and can be cited by the team | The next similar task cites it; "Cited N times" starts accumulating |
| **Reject** | The asset **doesn't enter the knowledge base**; the team can't cite it | This piece of distilled knowledge doesn't take effect |

Two things to know about rejection:

- **What's rejected is "this asset", not the process behind it** — the original messages and files it was distilled from are still in project memory and the workspace (memory = process, knowledge = distilled, see [10 Core Concepts · Memory and Context · Knowledge base and memory: division of labor](10-05-Memory-and-Context.md#knowledge-base-and-memory-division-of-labor)). If you need it again later, you can go back to that process and have the team distill it once more;
- Leaving pending assets piled up without taking a stance means the team keeps working "without this knowledge" — approve or reject in time so the distillation keeps moving.

![](docs/assets/S45-待审资产的批准与驳回（知识卡操作区）.png)

## Retire and restore

**Retiring** makes an "Active" asset **stop taking effect** — the team no longer cites it, but the asset itself, its evidence list, and its citation trail are all kept. When retiring fits:

- The knowledge is **outdated** (the stance changed, the practice was replaced, the pitfall was fixed);
- A convention only applies to a specific phase, and once the phase is over you don't want it influencing the team anymore.

Retiring isn't the end — **restoring** can turn the asset back into "Active", and the team can cite it again after the restore. So retire / restore fits knowledge that's "not needed for now, but might be again".

> **Tip**: want to keep it but are sure the team shouldn't cite it anymore → retire; don't even need to keep it → permanently delete (next section). **Retiring is reversible; deleting is not.**

## Permanent delete: irreversible

**Permanently deleting** is a knowledge asset's terminal operation: the asset disappears from the knowledge base, **unrecoverable** — there's no recycle bin, no undo. Confirm two things before you do it:

1. **Is this knowledge really no longer needed?** If it's only temporarily unused, "retire" is enough — you don't need to go as far as deleting;
2. **Deleting is irreversible** — to have the team use it again, you can only re-distill it from the process.

What's deleted is "this one distilled piece", not the original records behind it: the evidence messages and files belong to memory and the workspace, and they don't disappear because the asset is deleted (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)).

![](docs/assets/S46-彻底删除的确认（知识卡操作区）.png)

## Citation trail and evidence deep-dive

The four pieces of information on a knowledge card (**Cited N times, M sources, evidence list, citation trail**, see [20 Guides · Knowledge Base and Skill Packs · The knowledge card: citations, sources, and evidence](20-06-Knowledge-Base-and-Skill-Packs.md#the-knowledge-card-citations-sources-and-evidence)) together form an evidence chain you can follow all the way down:

| From the card | Drill to | What question it answers |
|:--|:--|:--|
| **Evidence list** | The sources backing this knowledge (messages, files) — each one traceable to its origin | Is this knowledge reliable? What's it based on? |
| **Citation trail** | The tasks / projects that cited this knowledge | What work has this knowledge influenced? |

**Evidence deep-dive** means following that chain to the end: question a piece of knowledge → open the original message or file from the evidence list and see what it was distilled from; want to know a piece of knowledge's actual impact → jump from the citation trail back to the task that cited it. Any asset can answer "where it came from, who used it, whether it's reliable" — which is exactly what sets it apart from ordinary chat history.

> Entry point: a knowledge card is one of the six target types of [Global search](20-07-Search-Favorites-and-Contacts.md#the-six-search-target-types) — once you've found a knowledge card with ⌘K, you can keep drilling down through the evidence list / citation trail.

## Common questions

**Q: Where did a rejected asset go? Can it be recovered?**
After a rejection, the asset doesn't enter the knowledge base and the team can't cite it; the process behind it (messages, files) stays in project memory. If you need this knowledge again later, you can re-distill it from that process.

**Q: Which should I use: retire or delete?**
Not needed for now but might be again → **retire** (recoverable); sure you'll never need it → **permanently delete** (irreversible). When in doubt, retire.

**Q: Can a permanent delete be undone?**
No. It's an irreversible operation — confirm you no longer need it before deleting.

**Q: How far can the evidence deep-dive go?**
From the evidence list you can trace back to the original messages / files, and from the citation trail to the tasks that cited it — every layer jumps back to the source.

## Next steps

- Want to manage the three types of skill packs? → [40 Advanced · Skill Pack Management](40-02-Skill-Pack-Management.md)
- Want to review the basics of knowledge assets (five types, two scopes, the knowledge card)? → [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md)
- Want to understand the boundary between memory and knowledge? → [10 Core Concepts · Memory and Context · Knowledge base and memory: division of labor](10-05-Memory-and-Context.md#knowledge-base-and-memory-division-of-labor)

---

**Previous**: [30-04 Account and Identity](30-04-Account-and-Identity.md)
**Next**: [40-02 Skill Pack Management](40-02-Skill-Pack-Management.md)
