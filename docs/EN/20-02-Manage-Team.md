<!--
  Page: 20 Guides · Manage the Team
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Manage the Team
  Status: published (fourth batch)
-->

# 20 Guides · Manage the Team

> **At a glance**: the roster is your "team's live status panel" in every project — who's busy, who's idle, who's been archived, at a glance. This page teaches you: how to read the status dots and the sorting, how to add members, how to remove members (archive, not delete — history is always kept), how to invite departed members back, and how to **Stop** a member who is working (inline double confirmation, auto-cancels in 5 seconds). Why every team change needs approval is in [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md); this page only covers the operations.

**On this page**

- [The roster: the team's live status](#the-roster-the-teams-live-status)
- [Status dots: who's busy, who's idle](#status-dots-whos-busy-whos-idle)
- [Sorting and gray-out: busy first](#sorting-and-gray-out-busy-first)
- [Adding members: let the Coordinator propose](#adding-members-let-the-coordinator-propose)
- [Removing members: archive, not delete](#removing-members-archive-not-delete)
- [Inviting back: pull departed members back](#inviting-back-pull-departed-members-back)
- [Stop: interrupt a working member](#stop-interrupt-a-working-member)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## The roster: the team's live status

Every project has a **member roster** panel on the right, showing "Members · N" (the current member count) at the top, and below it one row per member:

- **Avatar and name** — each member has a name and a lifelong avatar, unique within the project;
- **Role tag** — like "Frontend" or "Data analysis", telling you where this member's judgment lies;
- **Status dot** — the small dot next to the name, reflecting in real time what the member is doing right now (see the next section).

The roster isn't decoration: glance at it before assigning work, and you know who the Coordinator should assign to, who's busy, and who can take the next task.

![](docs/assets/S20-成员花名册全景.png)

## Status dots: who's busy, who's idle

The status dot comes in four colors/styles, matching four statuses:

| Status | Meaning | Typical scenario |
|:--|:--|:--|
| **Idle** | In the project, no current task, ready to take work | Just joined; finished one task and waiting for the next |
| **Working** | Executing a confirmed Task card | While the member's streaming output is in progress |
| **On standby** | In the project, temporarily not executing a task | Just stopped, or waiting for the next round of assignments |
| **Archived** | Removed from the project, history kept, grayed out | After removing a member (see [Removing members](#removing-members-archive-not-delete)) |

> **Key point**: status is "of this moment" and changes automatically with the work — it becomes **Working** after a task is confirmed, and returns to **Idle** after the report is submitted and the Coordinator verifies. You don't need to change it by hand; only **Stop** and **Archive** require your action.

## Sorting and gray-out: busy first

The roster isn't sorted randomly — it's ordered by "who most needs your attention":

- **Busy first** — members with status **Working** are at the top, so you see at a glance who's working and who's occupying resources;
- **Idle and On standby** come next — the "available to take work" pool;
- **Archived members** come last, with the **whole row grayed out** — they're in the project, but no longer in the working lineup.

Gray-out isn't "deleted": an archived member's history bubbles and output records are all still in the chat stream, ready to look up anytime (see the next section).

## Adding members: let the Coordinator propose

When you need extra hands, just say so in the group chat and let the Coordinator propose — **adding members is initiated by the Coordinator; you only have the right to confirm**:

> **You (to the project group chat)**
> This batch of feedback is a bit heavy. @Coordinator could we add a Marketing member to help write the callback scripts?

The Coordinator will judge the request, then pop up a **Build Team** Approval Card listing the candidate members (avatar, name, role, a one-line "Good at" note). After you confirm, the member joins, and the roster gains one **Idle** row.

- Want someone specific in or out? Say it on the card or in the group chat, and the Coordinator will adjust the candidates and pop the card up again;
- The detailed steps for adding members are in [20 Guides · Create a Project and Build a Team · Step 4](20-01-Create-Project-and-Build-Team.md#step-4--confirm-the-build-team-card-build-the-team).

## Removing members: archive, not delete

To remove a member, again have the Coordinator propose **Remove a member**: the Coordinator pops up a **Remove a member** Approval Card, listing the member to remove (name + role + current status) and the reason for removal. After you confirm:

- The member becomes **Archived**, kept and grayed out in the roster;
- **All history bubbles and output records are kept** — what they said in the group chat and the files they handed in always keep their provenance;
- Archiving isn't deleting — Knowe has no "erase a member" action; a project's history is always traceable.

> **Tip**: archiving a member stops them from taking new work, but doesn't affect the files they produced before. If you just want someone to take a break, you can skip archiving and simply not assign work to them while they're Idle.

![](docs/assets/S21-移除成员审批卡.png)

## Inviting back: pull departed members back

An archived member can be **invited back** anytime — say the word in the group chat, the Coordinator proposes adding them (the Build Team card), and after you confirm the member rejoins:

> **You (to the project group chat)**
> The callback scripts need hands again. @Coordinator let's invite 沈星 back.

A member who comes back **isn't an amnesiac newcomer**: project memory travels with the project (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)), so their previous context in this project is still there — they can pick up where they left off.

## Stop: interrupt a working member

When a member is **Working**, if you notice the direction is wrong, the work was assigned to the wrong person, or they've been silent for too long, you can **Stop** them:

1. In the roster, find the member whose status is **Working** and click the "**Stop**" button on their row;
2. Clicking it **doesn't execute immediately** — an **inline double confirmation** appears (something like "Confirm stopping 顾言's current task?"), to prevent accidental clicks;
3. **If you don't confirm within 5 seconds, the operation auto-cancels** and the task continues as before;
4. After you confirm, the member interrupts the current work and the status returns to **On standby**; **the intermediate output already produced and the records in the chat stream are all kept** — it's just that this run stops here.

After stopping, if you want to continue, just assign the task again — what was stopped is "this one execution", not the member.

![](docs/assets/S22-停止成员的行内二次确认.png)

> **Design rationale**: like the Approval Cards, "Stop" makes key actions pass through you — but the 5-second auto-withdrawal is there to **leave no room for misoperation** — see [10 Core Concepts · Approval Mechanism · Stop and double confirmation](10-04-Approval-Mechanism.md#stop-and-double-confirmation).

## Common questions

**Q: What's the difference between archiving and deleting?**
Archiving means "removed from the working lineup but the full history is kept" — grayed out and invitable back. Knowe has no delete-member operation — every sentence said in the project and every file handed in keeps its provenance.

**Q: Does Stopping lose progress?**
It doesn't lose what was already produced: files a member wrote are in the project directory, and the chat records are in the workspace memory. What's lost is only "this unfinished run" — assign it once more and you're back on track.

**Q: Does an invited-back member still remember this project?**
Yes. Project memory travels with the project, not with the member; when they come back, the previous context, preferences, and decisions are all still there (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)).

**Q: A member has been "Working" without moving — what should I do?**
First check whether their streaming output is still progressing; if it's really stuck, interrupt them with this page's [Stop](#stop-interrupt-a-working-member) operation, then assign the task again. If the connection status is abnormal, see [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md).

**Q: Do adding and removing members really need approval?**
Yes. Changes at the "people and work" level stay your call — the AI can only propose. That's Knowe's safety design, not a step you can skip (see [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)).

## Next steps

- The team is managed — start assigning and accepting → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- Back to the full steps for creating a project → [20 Guides · Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md)
- Want to understand why team changes pass through you? → [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)
- Want to know the 24 roles' "Good at" and "Not suitable for"? → [50 Reference · Roles Catalog](50-01-Roles-Catalog.md)

---

**Previous**: [20-01 Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md)
**Next**: [20-03 Assign and Accept](20-03-Assign-and-Accept.md)
