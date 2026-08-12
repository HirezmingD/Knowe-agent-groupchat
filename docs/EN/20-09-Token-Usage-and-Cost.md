<!--
  Page: 20 Guides · Token Usage and Cost
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Token Usage and Cost
  Status: published (sixth batch)
-->

# 20 Guides · Token Usage and Cost

> **At a glance**: the team's work calls models, and model calls cost money — Knowe has a built-in local **Token usage dashboard** that lets you see exactly where every cent goes. This page follows the dashboard's reading order: filter by date range → stat cards (total Token / cost, and more) → trend chart → breakdown table (two tabs: by model / by member); amounts are shown in RMB (¥). It ends with **common ways to save tokens**, all actionable within this product's existing mechanics.

**On this page**

- [The usage dashboard: how to read it from top to bottom](#the-usage-dashboard-how-to-read-it-from-top-to-bottom)
- [Filtering by date range](#filtering-by-date-range)
- [Stat cards and the trend chart](#stat-cards-and-the-trend-chart)
- [The breakdown table: by model / by member](#the-breakdown-table-by-model--by-member)
- [How cost is calculated](#how-cost-is-calculated)
- [Common ways to save tokens](#common-ways-to-save-tokens)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## The usage dashboard: how to read it from top to bottom

The dashboard's reading order is the order of **drilling down into the data**:

> **Filter by date range → stat cards (total Token / cost, and more) → trend chart → breakdown table (two tabs: by model / by member)**

First bracket the time range, then look at the totals, then watch the trend, and finally drill down to the detail of "who, on which model, spent how much". The dashboard is **local** (Knowe's built-in local Token usage dashboard) — open it anytime, with no dependence on external bills.

![](docs/assets/S37-Token 用量面板总览.png)

## Filtering by date range

At the top of the dashboard is the **date-range filter**: once you pick a start and end date, the stat cards, the trend chart, and the breakdown table all follow that range.

Typical uses:

- **Reconciliation** — how much did this month / this week cost in total;
- **Review** — which days last week, or which task types, consumed the most;
- **Comparison** — after changing models or the team setup, did usage come down?

## Stat cards and the trend chart

- **Stat cards** — the summary numbers for the selected range: total Token, cost, and more — the totals at a glance;
- **Trend chart** — the curve of usage over time; spot the peaks and troughs and pin down "which day was especially heavy".

The two work together: the stat cards answer "how much in total", and the trend chart answers "when it was spent".

## The breakdown table: by model / by member

The breakdown table uses **two tabs** to switch between two dimensions:

| Tab | What you see | Best at answering |
|:--|:--|:--|
| **By model** | How many tokens each model used, and its cost | Which model is the cost driver; comparing costs across models |
| **By member** | How much each member used | Who's steadily occupying resources (the more members, and the more active they are, the more they occupy — see [10 Core Concepts · Approval Mechanism · Why every team action needs approval](10-04-Approval-Mechanism.md#why-every-team-action-needs-approval)) |

Stack the two dimensions and you can pin down the combined cost of "which member, running on which model" — the big spenders are obvious at a glance.

![](docs/assets/S38-明细表「按模型 按成员」双 Tab.png)

## How cost is calculated

- **Amounts are in ¥** — costs on the dashboard are always shown in RMB;
- Cost depends on the **models you actually call and the Token usage**: the models are configured and chosen by you (see [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)), and the usage comes from the team's actual work;
- Usage and cost are tallied on this machine (the local dashboard) — check anytime, handy for weighing cost against value.

## Common ways to save tokens

Saving tokens isn't "using less" — it's "wasting less on the same work". Every practice below maps to a mechanism Knowe already has:

| Practice | Why it saves | Where to learn more |
|:--|:--|:--|
| **Keep the team just big enough** | Every member continuously occupies model calls and tokens; the fewer people, the less occupation | [Create a Project and Build a Team · Common questions](20-01-Create-Project-and-Build-Team.md#common-questions) |
| **Archive idle members** | Archived members take no new work and occupy nothing | [Manage the Team · Removing members: archive, not delete](20-02-Manage-Team.md#removing-members-archive-not-delete) |
| **Stop stuck members in time** | Stopping one run avoids burning tokens on idle spinning | [Manage the Team · Stop: interrupt a working member](20-02-Manage-Team.md#stop-interrupt-a-working-member) |
| **Write clear acceptance criteria into the instruction** | Fewer "redo → redo" repeated calls | [Assign and Accept · Common assigning patterns](20-03-Assign-and-Accept.md#common-assigning-patterns) |
| **Reuse distilled knowledge** | Don't re-step on traps or re-explain; the same kind of work gets cheaper each time | [Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md) |
| **Understand the memory layering** | Older content is compressed into a rolling summary; context never grows without bound | [Memory and Context · The three layers of project memory](10-05-Memory-and-Context.md#the-three-layers-of-project-memory) |

> **Going further**: bind more suitable models to specific members (**per-member model binding**) — high-value tasks get the good model, low-value tasks get the cheaper option — see [30 Configuration · Models and Providers](30-01-Models-and-Providers.md).

## Common questions

**Q: What currency are the costs shown in?**
RMB (¥).

**Q: What's the scope of the usage statistics?**
The date range you selected; the dashboard is local, and it counts the model calls that happened locally.

**Q: Why is one member's cost especially high?**
Look at how many tasks they were assigned, how many files they were fed, and how long they kept working — drill down in the breakdown table's "by member" tab, then switch to "by model" to see which model they use, and you can pin down the cause.

**Q: How do I bring the cost down?**
Start with the [Common ways to save tokens](#common-ways-to-save-tokens) section: control the team size, archive idle members, stop stuck members in time, write clear acceptance criteria into instructions, and reuse knowledge to cut repetition.

**Q: Can the dashboard's data be exported?**
This page only covers how to read the dashboard; the current version doesn't offer usage export — check the details in the dashboard.

## Next steps

- Want to control models and cost at the source (per-member model binding, and more)? → [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)
- Want to review how assigning work avoids rework? → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- Want to understand why memory saves context? → [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)
- Want to view files in the preview window? → [20 Guides · File Preview Window](20-08-File-Preview-Window.md)

---

**Previous**: [20-08 File Preview Window](20-08-File-Preview-Window.md)
**Next**: [30-01 Models and Providers](30-01-Models-and-Providers.md)
