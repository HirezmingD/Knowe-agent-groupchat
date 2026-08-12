<!--
  Page: 20 Guides · Assign and Accept
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Assign and Accept
  Status: published (fourth batch)
-->

# 20 Guides · Assign and Accept

> **At a glance**: this is the core of Knowe's main loop: you state the request to the Coordinator, it uses the **Task card** to assign the work to a Worker, you confirm, the Worker starts working, and when it's done the Coordinator **verifies in person** before reporting back to you. This page breaks down one complete round: the Task card's three fields (who / the task / the Coordinator's note), Confirm / Reject / revision feedback (feedback history), the streaming process and the reasoning panel when a member starts working, and how the Coordinator verifies the deliverable — ending with a complete copy-ready sample conversation "write a weekly report and export it to PDF".

**On this page**

- [A complete round of assigning work](#a-complete-round-of-assigning-work)
- [The Task card in detail: three fields](#the-task-card-in-detail-three-fields)
- [Confirm, Reject, and revision feedback](#confirm-reject-and-revision-feedback)
- [The member starts working: streaming output and the reasoning panel](#the-member-starts-working-streaming-output-and-the-reasoning-panel)
- [After the report: how the Coordinator verifies](#after-the-report-how-the-coordinator-verifies)
- [Example round: write a weekly report and export it to PDF](#example-round-write-a-weekly-report-and-export-it-to-pdf)
- [Common assigning patterns](#common-assigning-patterns)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## A complete round of assigning work

Assigning work isn't "sending a message" — it's a complete closed loop. Keep this diagram in mind; the rest of this page explains each link of it:

```text
You state the request ──► The Coordinator breaks it down ──► Task card (waits for your stance) ──► You confirm ──► The member starts working
                                                                          │                        │
                                                                          ▼                        ▼
    The Coordinator reports ◄── The Coordinator verifies (reads files) ◄── The member submits a report ◄── Streaming output
```

The key point: there's a **gate in the middle — the Task card**. After breaking down the task, the Coordinator doesn't start working directly; it first sends the task to you for review. Until you confirm, the member doesn't move. This is Knowe's "the AI can only propose; you have the final say" made real in assignment (see [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)).

## The Task card in detail: three fields

An "Assign a task" Task card usually has three fields:

| Field | What it is | Who decides it | Example |
|:--|:--|:--|:--|
| **Who it's assigned to** | The target member's name and role | The Coordinator proposes; you can ask to change the person | "顾言 · Technical writing" |
| **What to do** | What exactly to do — the goal and the acceptance criteria | The Coordinator breaks it down from your request | "Read the materials under docs/, write the Weekly Progress Report, including the three sections: completed this week / issues encountered / next week's plan" |
| **The Coordinator's note** | Extra guidance for the member, not in the instruction but affecting how it's done | The Coordinator | "Base the data on the materials under docs/ — don't make things up; the PDF and md share the same name" |

Besides the three fields, the card may carry **feedback history from during the approval** — what feedback you gave and what the Coordinator adjusted are all recorded on the card (see the next section).

![](docs/assets/S05-派发任务任务卡.png)

## Confirm, Reject, and revision feedback

Once the Task card pops up, you have three options:

| Your action | Result | When to use it |
|:--|:--|:--|
| **Confirm** | The task is assigned immediately; the member starts working | The task is fine as is |
| **Reject** | The task is canceled; the Coordinator re-breaks it down or switches the plan | Wrong direction, wrong person, unreasonable instruction |
| **Give revision feedback** | The feedback is written into the card's feedback history; the Coordinator **pops the card up again** after revising, and you state your stance anew | Largely fine, but details need tuning |

Revision feedback is the most frequent operation day to day — it's lighter than Reject and steadier than a blind Confirm. Just write in the feedback box on the card, for example "PDF in A4 layout", "This should be assigned to a Data analysis member", "Add to the note: data as of this Friday". After the Coordinator revises, the card pops up again with the feedback history; you look it over and Confirm or keep revising.

> **Tip**: Reject ≠ a broken relationship, and timing out ≠ agreeing by default. The four final states of an Approval Card and the timeout rules are in [10 Core Concepts · Approval Mechanism · Countdown and the four final states](10-04-Approval-Mechanism.md#countdown-and-the-four-final-states). To change the approval timeout, adjust it in Settings (see [30 Configuration · Approvals, Notifications, and the Tray · Approval timeout](30-02-Approvals-Notifications-and-Tray.md#approval-timeout)).

## The member starts working: streaming output and the reasoning panel

After you confirm the Task card, the member starts working. The group chat now shows **their working process** — instead of waiting until it's done and then seeing a summary pop up:

- **Streaming output** — the member types as it works, content appearing word by word, so you can see in real time what it's writing;
- **Work stage hints** — like "reading docs/ materials → organizing this week's completions → writing weekly-report.md → exporting weekly-report.pdf", letting you know which stage it's at;
- **Reasoning panel** — the member's thinking process (why it breaks things down this way, what it ran into, what it plans to do); expand it if you want to see, collapse it if you don't.

The meaning of this process: **trust is built on visibility**. You're not waiting for a black-box conclusion — you're watching a member actually work (for the mental model, see [10 Core Concepts · Lead an AI Team by Chat](10-01-Lead-a-Team-by-Chat.md)).

![](docs/assets/S24-成员流式工作过程与推理面板.png)

## After the report: how the Coordinator verifies

When the member finishes, it submits a **report**, and the produced files appear in the chat stream as **File cards**. But note: **the task isn't over here** — next comes the Coordinator's verification step.

The Coordinator **doesn't trust the report blindly**. It will:

1. **Read the files to verify** — open the actual files in your workspace directory and check them item by item against the task instruction (for example, read `weekly-report.md` and confirm all three sections are there and the data matches the materials under `docs/`; check that `weekly-report.pdf` really exists and opens);
2. **Only after verification** does it report back to you — telling you what was completed, where the files are, and whether anything deviated;
3. If there's a deviation, it says so directly, or sends it back to the member to redo — rather than hiding the problem.

So the real sign that "this round is truly done" is: **the Coordinator explicitly reports completion and points to where the deliverables are** — not just "the member submitted a report". You can also open the workspace directory anytime and check yourself (see [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)) — it's your turf.

![](docs/assets/S06-成员报告 + 文件卡片 + 项目经理验收汇报.png)

## Example round: write a weekly report and export it to PDF

Below is a complete round's conversation script, **lines marked "(you act)" need your input**. This example is based on a "Jianji" project that's already set up, with **顾言 (Technical writing)** and **江澈 (Data analysis)** as members; if your project doesn't have them yet, add them first under [20 Guides · Manage the Team · Adding members](20-02-Manage-Team.md#adding-members-let-the-coordinator-propose).

**Scenario**: the week is over, and you want the team to organize this week's product progress into a weekly report and export it to PDF.

> **You (to the project group chat)**
> @Coordinator write a weekly progress report for this week, export it as a PDF, and put it in the project directory. Content in three sections: features completed this week, issues encountered, next week's plan. The commit records and requirements docs for this week are under docs/ — let the members read them themselves; don't make anything up.
>
> **Coordinator**
> Got it. 顾言 (Technical writing) is the best fit for this — she reads the materials under docs/ first, then writes the report, then exports the PDF. I'll assign it first; once you confirm, she starts.

**(you act)** On the "Assign a task" card, check the three fields: who it's assigned to (顾言 · Technical writing), the task instruction (read the docs/ materials → write the Weekly Progress Report, with the three sections completed this week / issues encountered / next week's plan → export the PDF to the project directory), the Coordinator's note (base the data on the materials under docs/, don't make things up; the PDF and md share the same name). Click **Confirm** when it all looks right.

> **顾言 (Technical writing) is working…** (streaming output: reading docs/ materials → organizing this week's completions → writing weekly-report.md → exporting weekly-report.pdf)
>
> **顾言 (Technical writing) submits the report** (with File cards: `weekly-report.md`, `weekly-report.pdf`)
> The weekly report is done and the PDF is exported. All three sections are complete, and the data all comes from the materials under docs/.
>
> **Coordinator** (after verifying the files)
> I've checked: all three sections of `weekly-report.md` are there, and the data matches the commit records and requirements docs under docs/; `weekly-report.pdf` is also in the project directory and opens fine. Click the File cards in the chat to preview. The 4 features completed this week, 2 open issues, and the 3 planned items for next week are all in. Want any wording or layout tweaks? Just say so.

**If you gave revision feedback on the card**, the round gains a short extra segment — for example, you wrote "PDF in A4 layout, add a table of contents page" on the card:

> **Coordinator**
> Adjusted per your feedback: the PDF is now in A4 layout, with a table of contents page at the start. The Task card popped up again — want another look?

**(you act)** On the re-popped Task card, click **Confirm**; 顾言 redoes the work per the new requirements, and the flow continues as above.

> **Note**: this is an example that mirrors the product's real interactions — the Coordinator breaks the task down and adds a note, you confirm the Task card, the member streams its work and submits a report, and the Coordinator reads the files and verifies before reporting. Actual wording may vary a bit with the model and project content, but the flow and the card shapes stay the same.

## Common assigning patterns

| Pattern | How to trigger it | Trait |
|:--|:--|:--|
| **One at a time** (default) | Give the Coordinator one request in the group chat; it breaks out the first task | Complete one, accept one — steady rhythm, fewer reworks |
| **Several in parallel** | Explicitly say "these two things can be done at the same time" | The Coordinator breaks it into multiple Task cards, each needing your confirmation |
| **Work with acceptance criteria** | Write "Acceptance criteria: …" into the request | The Coordinator writes the criteria into the task instruction, so verification has a basis |
| **Feedback iteration** | After acceptance, say "change this part" | The Coordinator brings the feedback back and assigns again (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md) — feedback is written into project memory and isn't lost) |
| **Redo with someone else** | Say "have someone else do this" | The Coordinator adjusts the Task card's target member and pops it up again |

> **Tip**: the default rhythm is "one at a time". Don't over-commit on the first assignment — let the Coordinator deliver one thing, you accept it once, and speed up once you're familiar.

## Common questions

**Q: What if the Task card times out?**
Timing out is an auto-withdrawal — it's not a Reject, and it's not agreeing by default. To keep the task going, just have the Coordinator propose it again (rules in [10 Core Concepts · Approval Mechanism · Countdown and the four final states](10-04-Approval-Mechanism.md#countdown-and-the-four-final-states)).

**Q: Can I change "who it's assigned to" on the Task card?**
Yes. Say you want a different person on the card or in the group chat, and the Coordinator will adjust the target member and pop the card up again. Choosing people is based on role judgment — see [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md).

**Q: Can I trust the report a member submits?**
The Coordinator reads the files and verifies — it doesn't trust reports blindly; that's product behavior, not a verbal agreement. But the final gate is yours: open the workspace directory and check anytime — those are your files (see [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)).

**Q: A member has been "Working" without moving — what should I do?**
Check whether their streaming output is still progressing; if it's really stuck, **Stop** them in the roster (see [20 Guides · Manage the Team · Stop](20-02-Manage-Team.md#stop-interrupt-a-working-member)), then assign the task again.

**Q: Can I assign several tasks at once?**
Yes, but every Task card needs your confirmation. To have the team work in parallel, explicitly tell the Coordinator "do these two things at the same time".

## Next steps

- Want to communicate efficiently in the group chat and DM members? → [20 Guides · Group Chat and DMs](20-04-Group-Chat-and-DM.md)
- Want to manage the team (add, invite back, Stop)? → [20 Guides · Manage the Team](20-02-Manage-Team.md)
- Want to understand why breaking down and verifying are the Coordinator's job? → [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)
- Want to review the complete main loop? → [01 Quickstart](01-Quickstart.md)

---

**Previous**: [20-02 Manage the Team](20-02-Manage-Team.md)
**Next**: [20-04 Group Chat and DMs](20-04-Group-Chat-and-DM.md)
