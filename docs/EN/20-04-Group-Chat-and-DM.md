<!--
  Page: 20 Guides · Group Chat and DMs
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Group Chat and DMs
  Status: published (fifth batch)
-->

# 20 Guides · Group Chat and DMs

> **At a glance**: almost all your communication in Knowe happens in chat — the project group chat is the communication hub of the main loop, and direct messages (DMs) are the complement. This page teaches the chat basics: line breaks and the send shortcut in the composer (Ctrl/⌘+Enter), mentioning members with `@`, quoting / forwarding / favoriting / copying messages, and double-clicking a member in the roster to enter an **in-project DM** (`dm:project:member`) — plus one important boundary: **DMs are not private**.

**On this page**

- [Group chat: the communication hub of the main loop](#group-chat-the-communication-hub-of-the-main-loop)
- [The composer: line breaks and sending](#the-composer-line-breaks-and-sending)
- [Mentioning members](#mentioning-members)
- [Message actions: Quote / Forward / Favorite / Copy](#message-actions-quote--forward--favorite--copy)
- [In-project DMs: double-click a member in the roster](#in-project-dms-double-click-a-member-in-the-roster)
- ["DMs are not private": boundary explanation](#dms-are-not-private-boundary-explanation)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Group chat: the communication hub of the main loop

The project group chat is each project's "main battlefield": requirements are stated here, Approval Cards pop up here, members' work processes are visible here in real time, and results are reported here. All key communication within a project goes through the group chat, for a simple reason — **group chat content is written completely into project memory** (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)), so the team shares one context and the Coordinator keeps the whole picture.

Two points for speaking in the group chat:

- **State requirements to the Coordinator** — use `@Coordinator` to make the message stand out; it breaks the requirements down and assigns the work (see [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md));
- **Actions that don't need approval can be done anytime** — daily chat, asking questions, and watching members work don't trigger approval; only "create a project / add a person / assign a task / remove a member" pop cards (see [10 Core Concepts · Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md)).

## The composer: line breaks and sending

The composer sits at the bottom, and there are two things to keep straight:

| Key | What it does |
|:--|:--|
| **Enter** | **Line break** — doesn't send |
| **Ctrl + Enter** (written as Ctrl/⌘+Enter in the product) | **Sends** |

This design is deliberate for an AI-team product: instructions to the AI tend to be long and need multi-line writing. If you're used to pressing Enter to send, in Knowe you'll just keep getting line breaks — remember **Ctrl+Enter is what sends**. The composer also shows hint text (like "Message to project (Ctrl+Enter to send, Enter for a new line)") to remind you at all times.

Two more points:

- **Drafts are saved per conversation** — if you're halfway through writing and switch away, the content is still there when you come back; no need to worry about losing it;
- **Messages send optimistically** — once sent, the message appears in the chat stream immediately with a three-state marker (Pending / Delivered / Uncertain ⚠); seeing ⚠ means sending may have failed — check the connection status badge or resend.

![](docs/assets/S26-输入区与快捷键提示.png)

## Mentioning members

To get a certain person (or role) to notice your message, use **`@`**:

- Type `@` in the input box and the mentionable targets in the project pop up (the Coordinator and the Workers); pick one;
- You can also type a member's name directly — it's recognized as a mention in the message too;
- Mentioned targets get a more prominent notification — a good fit for "calling someone out to do work" or "following up on something".

Typical usage:

> **You (to the project group chat)**
> @Coordinator Is this week's weekly report done? @顾言 Double-check the data in the weekly report against the due dates once more.

> **Tip**: `@` is just "a call" — it doesn't trigger approval, and it doesn't auto-assign work. To assign, you still go through the Task card (see [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)).

## Message actions: Quote / Forward / Favorite / Copy

Hover over a message and an action menu appears, with four common actions:

| Action | What it does | Key detail |
|:--|:--|:--|
| **Quote** | Reply to a specific message | A **quote block** is generated under the message; the other side (or you) can click it to **jump back to the original** — the discussion gets a context anchor |
| **Forward** | Send the message to another conversation (project group chat, DM) | Forwards **with original formatting**, and you can **attach your own comment** |
| **Favorite** | Save an important message to Favorites | View them all in the **Favorites** view (see [20 Guides · Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md)) |
| **Copy** | Copy the message's original text | Handy for pasting into external tools |

Quote is the most common: say, while accepting work, you disagree with one sentence in a member's report — quote it directly and post a comment; the member clicks the quote block and jumps right to that sentence instead of hunting across the whole screen.

![](docs/assets/S27-消息操作菜单.png)

## In-project DMs: double-click a member in the roster

Besides the group chat, you can also **DM** a single member:

- **Double-click a member in the roster** to open a DM window with them (the conversation is identified in the `dm:project:member` form);
- DMs fit **adding background**: for example, giving one member an extra piece of context or asking a small question without spamming the group chat;
- In the conversation list, in-project DMs hang under their project, so you can come back anytime.

![](docs/assets/S28-群内私聊窗口.png)

**Note**: Zinnia's **Platform DM** is a different thing — it's the fixed window pinned at the top of the conversation list on the left; it doesn't belong to any project and is a separate reception channel (see [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)).

## "DMs are not private": boundary explanation

This is the most important boundary of DMs in Knowe, worth its own section:

> **In-project DMs are not private.** DM content is **written back to the owning project's memory**, and the Coordinator always knows what happened in DMs.

In other words: what you say in `dm:project:member` belongs to the project's context just like the group chat — it is not a secret channel independent of the project. **It fits "I don't want to spam the group chat but want the team to know" — not "I don't want the Coordinator to know".**

Why is it designed this way? Three reasons (details in [10 Core Concepts · Memory and Context · Why DM content is written back to the project](10-05-Memory-and-Context.md#why-dm-content-is-written-back-to-the-project)):

1. **The project is a whole** — a DM is private communication within the project, not an independent channel;
2. **Team collaboration needs complete information** — if the Coordinator doesn't know the background you give in a DM, its breakdown and verification become distorted;
3. **No "black-box communication"** — structurally rules out reaching agreements while bypassing the Coordinator.

> **Boundary reminder**: this rule targets **in-project DMs** (`dm:project:member`). Your Platform DM with Zinnia doesn't belong to any project — that's a separate reception channel. More details on DMs, memory, and permission boundaries are in [40 Advanced · DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md).

## Common questions

**Q: If I press Enter by accident, will the message be sent?**
No. Enter makes a line break; Ctrl+Enter is what sends — the composer shows hint text, and multi-line instructions are exactly why it's designed this way.

**Q: Does the Coordinator really know everything I say in a DM?**
Yes. DM content is written back to project memory, and the Coordinator always keeps the whole picture. "Telling only one person while hiding it from the Coordinator" isn't possible in Knowe — and it shouldn't be done anyway.

**Q: Can a quote block jump back to the original message?**
Yes. Click the quote block to jump back to the quoted message — however long the discussion gets, there's an anchor.

**Q: Does forwarding keep the original formatting?**
Yes. Forwarding carries the original formatting, and you can also attach your own comment.

**Q: Is my DM with Zinnia written back to the project too?**
No. Zinnia's Platform DM doesn't belong to any project — it's a separate reception channel (see [10 Core Concepts · Memory and Context · Why DM content is written back to the project](10-05-Memory-and-Context.md#why-dm-content-is-written-back-to-the-project)).

## Next steps

- Want to review the complete assign-and-accept round? → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- Want to manage the roster and the team? → [20 Guides · Manage the Team](20-02-Manage-Team.md)
- Want to understand how memory is kept across turns? → [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)
- Want to feed files to the team (images / PDF / Word / spreadsheets…)? → [20 Guides · Files and Attachments](20-05-Files-and-Attachments.md)

---

**Previous**: [20-03 Assign and Accept](20-03-Assign-and-Accept.md)
**Next**: [20-05 Files and Attachments](20-05-Files-and-Attachments.md)
