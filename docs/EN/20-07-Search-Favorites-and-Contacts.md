<!--
  Page: 20 Guides · Search, Favorites, and Contacts
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Search, Favorites, and Contacts
  Status: published (sixth batch)
-->

# 20 Guides · Search, Favorites, and Contacts

> **At a glance**: the more projects and messages, the harder it gets to find things fast. This page covers Knowe's find-and-organize trio: **Global search** (⌘K) — one command that reaches six types of targets directly; **favoriting** conversations and messages; and the **contact profile page** — openable for Zinnia / the Coordinator / Workers / groups, showing what they're good at, their current status, and the permission-boundary explanation covering "sandbox + memory + tools". There's also a handy side tool: each conversation's chat history drawer.

**On this page**

- [Global search: one command, straight to the target](#global-search-one-command-straight-to-the-target)
- [The six search target types](#the-six-search-target-types)
- [Favoriting conversations and messages](#favoriting-conversations-and-messages)
- [In-conversation search and the chat history drawer](#in-conversation-search-and-the-chat-history-drawer)
- [The contact profile page](#the-contact-profile-page)
- [Permission boundaries: sandbox, memory, and tools](#permission-boundaries-sandbox-memory-and-tools)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Global search: one command, straight to the target

Press **⌘K** to bring up the global search panel, type a keyword, and the results are grouped by target type — click one and you're there, no digging through menus layer by layer. It's called "global" because the search spans the whole app: conversations, messages, contacts, favorites, knowledge cards, and settings items can all be searched.

Typical scenarios:

- You remember something was said in a group, but not which message or which conversation — search the exact words and jump straight to the message;
- You want to open someone's profile page without hunting through the roster — search the name;
- You want to change a setting (like a notification switch) without digging through Settings — search the settings item.

![](docs/assets/S32-全局搜索面板（⌘K）.png)

## The six search target types

| Target | What you can search | Typical use |
|:--|:--|:--|
| **Conversations** | Project group chats, DMs | Type a project name to jump straight to the group chat |
| **Messages** | Message content in the chat stream | Search an exact phrase to locate that message |
| **Contacts** | Zinnia / the Coordinator / Workers / groups | Search a name to open the profile page |
| **Favorites** | Conversations and messages you've favorited | Search a favorite's title to jump straight to it |
| **Knowledge cards** | Knowledge assets in the knowledge base | Search a "pitfall"-type asset to jump straight to the card |
| **Settings items** | Entries in Settings | Search "notifications" to jump straight to the matching toggle |

> **Tip**: global search is the **cross-target** entry; to locate content **inside one** particular conversation, use [in-conversation search](#in-conversation-search-and-the-chat-history-drawer) for better precision.

## Favoriting conversations and messages

Favoriting is "pinning" important content so you can come back to it anytime:

- **Favorite a message** — hover over a message and click "Favorite" in the action menu (entry point: see [20 Guides · Group Chat and DMs · Message actions](20-04-Group-Chat-and-DM.md#message-actions-quote--forward--favorite--copy));
- **Favorite a conversation** — file a frequently used conversation into Favorites, then come back from the Favorites view with one click;
- **Favorites view** — view all favorites in one place, arranged by the two types "conversations / messages";
- In global search, favorites are one of the six target types too — search a favorite's title and you land on it directly.

## In-conversation search and the chat history drawer

When you need to dig through history in a long conversation, two things work together:

- **Chat history drawer** — every conversation can open its history drawer to browse past messages by time;
- **Message search** — the search function among message operations, locating keywords inside a conversation (see [20 Guides · Group Chat and DMs](20-04-Group-Chat-and-DM.md)).

Together with global search, that's a three-layer search: **global search** (find across conversations) → **message search** (find within a conversation) → **chat history drawer** (browse by time) — from "roughly where it is" to "exactly that sentence", there's always a way.

## The contact profile page

Zinnia, the Coordinator, Workers, and groups each have their own **profile page**. To open one: click the corresponding object in the roster, in a conversation, or in the global search results.

The most central block on a profile page is the **Good at** area:

| Object | What you'll see on the profile page |
|:--|:--|
| **Zinnia** | The platform-level host: helps you open projects and answers questions |
| **Coordinator** | The project's general manager: reads the project, breaks down tasks, assigns work, verifies deliverables |
| **Worker** | The role and the two boundaries "Good at / Not suitable for", plus the current status |
| **Group** | What this project is about and who's in it |

Besides the Good at area, the profile page also shows the **current status** (the status-dot level of a member, see [20 Guides · Manage the Team · Status dots](20-02-Manage-Team.md#status-dots-whos-busy-whos-idle)) and a **permission-boundary explanation** (see the next section).

![](docs/assets/S33-联系人资料页.png)

## Permission boundaries: sandbox, memory, and tools

The permission-boundary explanation on a profile page answers "what can this object actually touch", across three dimensions:

| Dimension | What it means |
|:--|:--|
| **Sandbox** | Which files it can read and write — by default only inside the workspace directory (see [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)) |
| **Memory** | Which context it can see — project memory, the knowledge base (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)) |
| **Tools** | Which capabilities it can call — the member's toolbox |

Why does the profile page carry this? Because in Knowe "what can be done" has explicit boundaries — you can look them up anytime instead of guessing. The full explanation of these three dimensions (including what gets masked) is in [40 Advanced · DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md).

![](docs/assets/S34-收藏视图.png)

## Common questions

**Q: What if global search can't find what I'm looking for?**
Try a shorter keyword; if the target is inside a conversation, "message search" locates it more precisely; for scenarios outside the six target types, global search doesn't cover them — use another entry (like the roster or Settings) instead.

**Q: Are favorites the same as other marks?**
Favoriting is Knowe's "pin": messages and conversations can both be favorited, collected in the Favorites view, and found by global search.

**Q: What can I see when I open a member's profile page?**
The Good at area (role + Good at / Not suitable for), the current status, and the permission-boundary explanation. To dig into "why assigning picks people by role", see [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md).

**Q: What's the relationship between contacts and the roster?**
The roster is the project's **member panel** (who's busy, who's idle); the contact profile page is each object's (Zinnia / Coordinator / Worker / group) **information page** — reachable from the roster or from search results.

## Next steps

- Want to review message operations (Quote / Forward / Favorite / Copy)? → [20 Guides · Group Chat and DMs](20-04-Group-Chat-and-DM.md)
- Want to meet the three kinds of roles and their division of labor? → [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)
- Want to look up knowledge cards and manage the knowledge base? → [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md)
- Want to open the file preview window? → [20 Guides · File Preview Window](20-08-File-Preview-Window.md)

---

**Previous**: [20-06 Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md)
**Next**: [20-08 File Preview Window](20-08-File-Preview-Window.md)
