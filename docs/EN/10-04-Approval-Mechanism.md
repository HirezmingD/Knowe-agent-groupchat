<!--
  Page: 10 Core Concepts · Approval Mechanism: The Boundary Between People and AI
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 10 Core Concepts · Approval Mechanism
  Status: published (third batch)
-->

# 10 Core Concepts · Approval Mechanism: The Boundary Between People and AI

> **At a glance**: in Knowe, every team action that involves "people and work" — creating a project, adding members, assigning tasks, removing members — waits for your stance in the form of an Approval Card. This page explains why it has to be designed this way (the AI can only propose), what each of the four kinds of Approval Cards looks like, what happens after you confirm, how the countdown and the four final states work, where to change the approval timeout, and the double confirmation when you Stop a member.

**On this page**

- [Why every team action needs approval](#why-every-team-action-needs-approval)
- [The four kinds of Approval Cards](#the-four-kinds-of-approval-cards)
- [Common elements on a card](#common-elements-on-a-card)
- [Countdown and the four final states](#countdown-and-the-four-final-states)
- [Changing the approval timeout](#changing-the-approval-timeout)
- [Stop and double confirmation](#stop-and-double-confirmation)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Why every team action needs approval

[Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md) already covered Knowe's collaboration paradigm: **you have the final say; the AI can only propose.** The approval mechanism is that sentence made real — it's the control plane between people and AI.

Why must "people and work" actions go through approval, instead of letting the team do as it pleases?

1. **"People and work" carry real cost** — adding a member means ongoing model calls and tokens; assigning a task means a member uses tools, reads and writes files, and may go online. None of this should be decided silently by the AI on your behalf.
2. **Trust is built on visibility** — every action you've confirmed with your own eyes, the team runs inside your field of view; an action you've never seen, the team can't take.
3. **The cost of mistakes lands on your side** — the files are written on your disk; you bear the consequences of the decisions. Control and responsibility should match, so the right to decide has to stay in your hands.

> **Tip**: only actions that "involve people and work" need approval. Everyday chatting, asking questions, and watching members work need no approval — approval is the gate reserved for decisions that "change the team's state or start work".

## The four kinds of Approval Cards

Knowe has exactly four kinds of Approval Cards, covering the four key actions in a team's lifecycle:

| Card | When it appears | What it looks like | What happens after you confirm |
|:--|:--|:--|:--|
| **Create Project** | After Zinnia helps you clarify what you need, she proposes opening a project | Project name input + "Choose directory" button (workspace directory **required**) + Confirm/Cancel + countdown | The project is created: the project group chat appears on the left, the Coordinator enters, Zinnia hands over |
| **Build Team** | After the Coordinator judges which roles the request needs, it proposes adding members | Candidate member list: avatar, name, role tag, a one-line "Good at" note | Members join: the roster updates, the member's status is **Idle**, ready to be assigned tasks |
| **Task** | After the Coordinator breaks a requirement into one piece of work, it proposes assigning it to a member | Who it's assigned to (name + role), the task instruction, the Coordinator's note; you can give revision feedback during approval | The member starts working: status changes to **Working**, and the streaming work process appears in the chat stream |
| **Remove a member** | The Coordinator proposes moving a member out of the project | The member to remove (name + role + current status), removal note | The member is removed and archived: grayed out in the roster but kept, history bubbles stay, and the member can be invited back anytime |

![](docs/assets/S16-四种审批卡的形态对照（四格拼图）.png)

How each field is filled and what each card looks like in detail is operational content — see [20 Guides · Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md), [20 Guides · Manage the Team](20-02-Manage-Team.md), and [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md). This page only covers the mechanism.

## Common elements on a card

The four cards differ in shape but share the same skeleton:

- **Title** — what kind of action this is (for example, "Assign a task");
- **Content** — the specifics of the action (who, what, the note, etc.);
- **Confirm / Reject** — your two explicit stances;
- **Countdown** — a card doesn't hang around forever, see below;
- **Feedback entry** — some cards (like the Task card) let you give revision feedback right on the card; the Coordinator will pop the card up again after revising.

## Countdown and the four final states

Every Approval Card carries a **countdown**; when it runs out, the card becomes invalid automatically. The countdown **follows the server-side clock** — no matter how fast or slow your computer's clock is, or whether the interface is on another window, the card's fate is timed by Knowe's backend and isn't reset by refreshing the frontend or switching windows.

A card that goes from popping up to closing has only four final states:

```
                   Approval Card (counting down)
                                │
     ┌────────────────┬───────┴────────┬────────────────┐
     ▼                ▼                ▼                ▼
     Approved         Rejected         Timed out        Canceled
     takes effect     action canceled  auto-withdrawn   withdrawn by
                      (can re-propose) (≠ rejected)     the proposer
                                                         (proposal void)
```

| Final state | What you did | The consequence |
|:--|:--|:--|
| **Approved** | Clicked "Confirm" | The action takes effect immediately (create project / join / start working / remove) |
| **Rejected** | Clicked "Reject" | The action is canceled; the Coordinator can propose again or switch the plan |
| **Timed out** | Did nothing; the countdown ran out | The card is auto-withdrawn and the action doesn't take effect — **timed out ≠ rejected**; this proposal just lapses |
| **Canceled** | — (withdrawn by the proposer) | The proposal is void and the card disappears |

> **Tip**: timing out doesn't mean you object — you just didn't get to it in time. To move forward, just have the Coordinator propose it again; the card's status stays in the chat stream and can be looked back at anytime.

## Changing the approval timeout

The countdown has a sensible default, and you can change it to your own pace:

- In **Settings**, you can change the approval timeout; the available durations are: **5 / 10 / 30 / 60 / 180 / 300 seconds, or "No limit"**.
- With "No limit", cards don't auto-time-out; they keep hanging until you state your stance (you can still Reject or Cancel manually).

The change takes effect immediately and affects cards that pop up afterward (cards already popped up keep the setting from when they appeared). Where exactly it lives in Settings, see [30 Configuration · Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md#approval-timeout).

## Stop and double confirmation

Besides the Approval Cards, there's one more action that needs your stance: **Stopping a member who is working**.

- In the **roster** on the right, a member whose status is Working can be clicked **Stop**;
- Clicking it doesn't execute immediately — an **inline double confirmation** appears (something like "Confirm stopping X's current task?"), and **if you don't confirm within 5 seconds it auto-cancels** — to prevent accidental clicks;
- After confirming, the member interrupts the current work and the status returns to Idle/On standby; the intermediate output already produced and the records in the chat stream are kept.

The double confirmation exists for the same reason as the Approval Cards: **key actions go through you, but leave no room for misoperation.** (The specific "Stop" steps are in [20 Guides · Manage the Team · Stop](20-02-Manage-Team.md#stop-interrupt-a-working-member).)

## Common questions

**Q: Isn't approval annoying — do I have to click on every sentence?**
No. Everyday conversation, asking questions, and watching members work don't trigger approval; only the four actions "create project / add members / assign tasks / remove members" pop up cards.

**Q: Does timing out count as agreeing by default?**
No. Timing out is an **auto-withdrawal**; the action doesn't take effect, and it doesn't represent your stance either.

**Q: If I reject a Task card, will the member hold a grudge?**
No. A rejection just cancels the action; the Coordinator will break the task down again or adjust the plan. The approval record stays in the chat stream for both sides to align on.

**Q: Can I turn approval off completely and let the team run itself?**
You can set the timeout to "No limit", but approval itself is a product boundary and can't be turned off — "the AI can only propose" is Knowe's safety design, not a step you can skip.

## Next steps

- Want to understand why every project needs a general manager in the middle? → back to [Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md)
- Want to pin down the project directory boundary? → [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)
- Want to understand how approval records distill into project memory? → [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)
- Want to click through the cards yourself? → [01 Quickstart](01-Quickstart.md)

---

**Previous**: [10-03 Projects and Workspaces](10-03-Projects-and-Workspaces.md)
**Next**: [10-05 Memory and Context](10-05-Memory-and-Context.md)
