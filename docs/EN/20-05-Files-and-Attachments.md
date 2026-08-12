<!--
  Page: 20 Guides · Files and Attachments
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · Files and Attachments
  Status: published (fifth batch)
-->

# 20 Guides · Files and Attachments

> **At a glance**: feeding files to the AI team is one of the most common actions in Knowe's main loop — requirements documents, reports, screenshots, and code can all be sent into the group chat as attachments for the members to read. This page covers four things: how to add attachments (pick or drag), which formats are supported, how the AI reads attachments (images read directly / documents packed into file content blocks), and one important safety guard — **only files you picked yourself are read**. It ends with common questions (what to do when the AI can't read a format).

**On this page**

- [Adding attachments: pick or drag](#adding-attachments-pick-or-drag)
- [Which formats are supported](#which-formats-are-supported)
- [How the AI reads attachments](#how-the-ai-reads-attachments)
- [The safety guard: only files you picked yourself are read](#the-safety-guard-only-files-you-picked-yourself-are-read)
- [File cards and preview](#file-cards-and-preview)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Adding attachments: pick or drag

There are two ways to bring files into the chat, and either works:

- **Pick** — click the attachment button in the composer and choose files in the system file picker;
- **Drag** — drag files straight from File Explorer into the composer.

After adding, an **attachment block** appears above the composer (file name + format icon, removable), and it sends along with the message. You can attach several files at once.

![](docs/assets/S29-拖拽附件进输入区.png)

Once sent, the attachment enters the group chat as part of the message: every member of the project can read it, and its content is also written into project memory (the process), so the Coordinator keeps the whole picture (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)).

## Which formats are supported

| Type | Notes |
|:--|:--|
| **Images** | Common image formats — screenshots, design mockups, photos, and more |
| **PDF** | Documents, reports, manuals, and more |
| **Word / Excel** | Office documents and spreadsheets (like .xlsx) |
| **Text and code** | Text files, source code files, Markdown, and more |
| **Other** | Common formats can all be tried; if one can't be read, see [Common questions](#common-questions) |

> **Tip**: the format list is just a statement of the "supported range" — whether the AI can actually read a specific file also depends on the file itself (corrupted, encrypted, and so on). What to do when "the AI can't read a format" is in [Common questions](#common-questions) below.

## How the AI reads attachments

An attachment isn't "throwing the AI a path and letting it guess" — the reading method splits by type into two:

- **Images go through multimodal direct reading** — the model directly "sees" the image content: UI screenshots, design mockups, charts, and text are all understood as the picture they are;
- **Other files are packed into file content blocks sent to the model** — documents, spreadsheets, and code first have their content extracted into file content blocks, then go to the model with the message.

In other words, what the model reads is **the file's content**, not a guess about "what this path points to". Attachment content enters the project context with the message, takes part in the members' work, and also counts toward model calls (usage can be checked in "20 Guides · Token Usage and Cost", see [20 Guides · Token Usage and Cost](20-09-Token-Usage-and-Cost.md)).

## The safety guard: only files you picked yourself are read

This is the most important file-related design, worth its own section:

> **Only files you picked yourself (by picking or dragging) are read by the AI.**

- The app's main process **sees and signs** the file's path; the backend only reads paths that were confirmed — this is the safety guard against being "tricked into reading arbitrary files";
- Attachments are the **sandbox's exception entrance**: by default, the team can only read and write the workspace directory you chose — it "can't get out" (see [10 Core Concepts · Projects and Workspaces · The workspace directory: the AI's sandbox](10-03-Projects-and-Workspaces.md#the-workspace-directory-the-ais-sandbox)); attachments are files you actively let through and point the team at;
- Read it the other way around: **the AI won't go read arbitrary files on your machine on its own**. Want it to read a file? Drag it in yourself.

> **In one sentence**: the land you fence in (the workspace directory) is where the team works; anything extra you want it to see, hand it in yourself.

## File cards and preview

Attachments and files produced by members both show in the chat stream as **File cards** (file name + format icon):

- **Attachments you send** — after sending, it's a message with an attachment;
- **Deliverables members hand in** — when a member submits a report, the produced files appear in the chat stream as File cards (see [20 Guides · Assign and Accept · After the report](20-03-Assign-and-Accept.md#after-the-report-how-the-coordinator-verifies)).

Click a File card to view the file's content in a **separate preview window** — HTML, images, PDF, Word, code, and more can all be previewed (rendering differences, zoom, and "Reveal in File Explorer" are in [20 Guides · File Preview Window](20-08-File-Preview-Window.md)).

## Common questions

**Q: What if the AI can't read a format?**
First check whether it's in the [supported formats](#which-formats-are-supported) list. If not, the common approach is to **convert the content into a supported format** before feeding it — for example, export it as PDF, text, or a spreadsheet, or **copy the key content straight into a message**. If a member reports an error like "the model doesn't support this format", troubleshooting ideas are in [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md).

**Q: Can other members see attachments sent to the group chat?**
Yes. Attachments are part of the project group chat's content: members can read them, and they're also written into project memory (the process) — it's the same logic as "DMs are not private": the project is a whole (see [10 Core Concepts · Memory and Context · Why DM content is written back to the project](10-05-Memory-and-Context.md#why-dm-content-is-written-back-to-the-project)).

**Q: Can I undo a wrong file I dragged in?**
Before sending, click "Remove" on the attachment block in the composer. After sending, it stays in the chat stream as a message — message operations (Quote / Forward / Favorite / Copy) are in [20 Guides · Group Chat and DMs](20-04-Group-Chat-and-DM.md).

**Q: Why can't the AI read any file on my computer directly?**
That's the security boundary design: by default the team only reads and writes the workspace directory; attachments are the only exception entrance, and even then they're read only after you pick them yourself and the app confirms and signs the path (see [The safety guard](#the-safety-guard-only-files-you-picked-yourself-are-read) on this page and [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)).

## Next steps

- Want the team to "understand you more the more you use it" and distill what you learn while reading files into knowledge? → [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md)
- Want to see how files render in the preview window? → [20 Guides · File Preview Window](20-08-File-Preview-Window.md)
- Want to understand why the workspace directory is the boundary? → [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)
- Want to review group chat communication and message operations? → [20 Guides · Group Chat and DMs](20-04-Group-Chat-and-DM.md)

---

**Previous**: [20-04 Group Chat and DMs](20-04-Group-Chat-and-DM.md)
**Next**: [20-06 Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md)
