<!--
  Page: 20 Guides · File Preview Window
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 20 Guides · File Preview Window
  Status: published (sixth batch)
-->

# 20 Guides · File Preview Window

> **At a glance**: files members hand in and attachments you send can all be viewed right in a **separate preview window** — no need to hunt for another app on your system. This page covers: how to open a preview (click the File card in the chat stream), the separate window and multiple tabs, how each format renders (HTML / images / PDF / Word / Excel / PPT / code / Markdown / plain text), zooming, and "Reveal in File Explorer" to locate the file itself.

**On this page**

- [Opening a preview: click the File card](#opening-a-preview-click-the-file-card)
- [A separate window with multiple tabs](#a-separate-window-with-multiple-tabs)
- [Rendering by file type](#rendering-by-file-type)
- [Zoom and "Reveal in File Explorer"](#zoom-and-reveal-in-file-explorer)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Opening a preview: click the File card

The **File card** in the chat stream is the entry to preview: click it, and the file opens in the separate preview window. Both kinds of File cards work:

- **Files a member produced** — when a member submits a report, the deliverables appear in the chat stream as File cards (see [20 Guides · Assign and Accept · After the report](20-03-Assign-and-Accept.md#after-the-report-how-the-coordinator-verifies));
- **Attachments you sent** — see [20 Guides · Files and Attachments](20-05-Files-and-Attachments.md).

The Quickstart follows the same path: click the File card in the chat stream and view it in the separate preview window (HTML / images / PDF / Word / code and more are all supported).

> **Tip**: preview is just "looking". The file itself always stays in the project's workspace directory — you can [Reveal in File Explorer](#zoom-and-reveal-in-file-explorer) anytime to work with it.

## A separate window with multiple tabs

The preview window has two design points:

- **A separate window** — the preview opens in its own window instead of taking over the chat's main interface; you can keep chatting in the group while looking at the preview;
- **Multiple tabs** — several files can be open at the same time, switched with tabs inside the window — no need to stack up a pile of windows.

![](docs/assets/S35-独立预览窗口与多标签页.png)

## Rendering by file type

Preview doesn't "open everything as text" — it **picks the presentation that best fits the file type**:

| Type | How it's previewed |
|:--|:--|
| **HTML** | Rendered as a web page |
| **Images** | Displayed directly |
| **PDF** | Shown page by page |
| **Word / Excel / PPT** | Rendered as a document / spreadsheet / presentation |
| **Code** | Code rendering (with highlighting) |
| **Markdown** | Displayed per the rendering rules, matching the chat stream |
| **Plain text** | Shown as text |

> **Note**: Markdown's rendering rules — GFM (GitHub Flavored Markdown) tables, formulas, code highlighting, and more — stay consistent with the chat stream; see [50 Reference · Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md) for details.

## Zoom and "Reveal in File Explorer"

- **Zoom** — the preview window supports zoom in / zoom out / fit window: handy for viewing large images, reading small text, and checking details;
- **"Reveal in File Explorer"** — one click locates the file itself in the system file manager. Most useful when you want to copy, move, or edit the file — the preview window is read-only; to edit a file, open it in the file manager.

The file itself lives in the project's workspace directory (see [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)) — that's your turf, and you can open it directly anytime.

## Common questions

**Q: What's the difference between previewing and opening a file directly?**
Preview happens inside the app — no need to install or hunt for an external app for each format; the file itself stays unchanged and can always be located in the file manager. Opening directly hands the file to the app associated with it on your system.

**Q: What if some files won't preview?**
First check whether the file type is in the [supported list](#rendering-by-file-type) and whether the file itself is corrupted. If what won't open is "the model can't read the content", that's a different matter — see [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md).

**Q: Does previewing modify the original file?**
No. Preview is read-only viewing; to edit a file, use "Reveal in File Explorer" to locate it, then open and change it with your own tools.

**Q: How many files can I preview at once?**
The multiple-tab design lets you open several files at the same time and switch between them with tabs — no need to open many windows.

## Next steps

- Want to know how to feed files to the team? → [20 Guides · Files and Attachments](20-05-Files-and-Attachments.md)
- Want the details of the rendering rules? → [50 Reference · Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md)
- Want to check how many tokens those files consumed? → [20 Guides · Token Usage and Cost](20-09-Token-Usage-and-Cost.md)
- Want to find a certain file fast in a project? → [20 Guides · Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md)

---

**Previous**: [20-07 Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md)
**Next**: [20-09 Token Usage and Cost](20-09-Token-Usage-and-Cost.md)
