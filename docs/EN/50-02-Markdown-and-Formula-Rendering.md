<!--
  Page: 50 Reference · Markdown and Formula Rendering
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 50 Reference · Markdown and Formula Rendering
  Status: published (tenth batch)
-->

# 50 Reference · Markdown and Formula Rendering

> **At a glance**: Knowe's chat stream (and the Markdown in the file preview) renders by one fixed set of Markdown rules. This page lists the supported syntax: GFM tables / strikethrough / task lists / autolinks, `$` / `$$` / `\[ \]` formulas (rendered by KaTeX), code highlighting with line numbers, single line breaks — plus one safety note: HTML is stripped. This page only lists the features explicitly named in the product's planned scope; other syntax features follow the product.

**On this page**

- [Where it applies: the chat stream and the file preview](#where-it-applies-the-chat-stream-and-the-file-preview)
- [The supported syntax list](#the-supported-syntax-list)
- [Formulas: rendered by KaTeX](#formulas-rendered-by-katex)
- [Code: syntax highlighting with line numbers](#code-syntax-highlighting-with-line-numbers)
- [Line breaks: a single newline](#line-breaks-a-single-newline)
- [Safety note: HTML is stripped](#safety-note-html-is-stripped)
- [Next steps](#next-steps)

---

## Where it applies: the chat stream and the file preview

These rendering rules apply to both places, with the same behavior:

- **The chat stream** — messages you and the team send render by these rules;
- **Markdown in the file preview** — `.md` files in the preview window are "displayed by the rendering rules, consistent with the chat stream" (see [20 Guides · File Preview Window · Rendering by file type](20-08-File-Preview-Window.md#rendering-by-file-type)).

> **Tip**: in the composer, Enter starts a new line and Ctrl+Enter sends — that's how multi-line instructions with formulas and code get written (see [20 Guides · Group Chat and DMs · The composer: line breaks and sending](20-04-Group-Chat-and-DM.md#the-composer-line-breaks-and-sending)).

## The supported syntax list

| Syntax | How to write it | What it does |
|:--|:--|:--|
| **GFM table** | `\| Column 1 \| Column 2 \|` | A pipe-separated table, first row is the header |
| **Strikethrough** | `~~text~~` | Adds a strikethrough to the text |
| **Task list** | `- [ ] not done` / `- [x] done` | Checkboxes you can tick |
| **Autolink** | Type the URL directly | A clickable link |
| **Inline formula** | `$formula$` | An inline formula rendered by KaTeX |
| **Block formula** | `$$formula$$` or `\[ formula \]` | A block formula on its own line, rendered by KaTeX |
| **Code** | Code fence + language name | Syntax highlighting + line numbers |
| **Line break** | A single newline | One newline displays as a line break |

> **Note**: GFM is GitHub Flavored Markdown — the basic syntax (headings, paragraphs, lists, bold, italics, quotes, links, images) works as usual; the table above only lists the features **explicitly named** in the product's planned scope.

## Formulas: rendered by KaTeX

- **Inline formulas**: `$...$` — the formula sits inside the text and flows with it;
- **Block formulas**: `$$...$$` or `\[ ... \]` — the formula gets its own line.

Formulas are rendered by **KaTeX**. Leaving a space around the formula makes it easier to read, for example:

> The mass–energy equivalence can be written inline as `$E = mc^2$`, or as a block formula `$$E = mc^2$$`.

## Code: syntax highlighting with line numbers

Wrap a code block in fences (three backticks) and name the language to get **syntax highlighting**; **line numbers** appear at the start of each line. For example, write this in a message:

````markdown
```python
def hello():
    print("hi")
```
````

(The example only shows the notation; the actual rendering follows the product.)

## Line breaks: a single newline

Unlike strict Markdown, where you need a blank line to start a new paragraph, Knowe shows **a single newline as a line break** — multi-line messages and item-by-item lists don't get squeezed into one block. That's also why the composer uses "Enter for a new line" (see [20 Guides · Group Chat and DMs · The composer: line breaks and sending](20-04-Group-Chat-and-DM.md#the-composer-line-breaks-and-sending)).

## Safety note: HTML is stripped

For safety, **HTML tags in messages or files are stripped** — they're not executed or rendered as web pages, and are only treated as Markdown text. To show a piece of code or a markup language itself, wrap it in a code block.

## Next steps

- Want to revisit how Markdown behaves in the file preview? → [20 Guides · File Preview Window](20-08-File-Preview-Window.md)
- Want to look up the unified terminology? → [50 Reference · Glossary](50-03-Glossary.md)

---

**Previous**: [50-01 Roles Catalog](50-01-Roles-Catalog.md)
**Next**: [50-03 Glossary](50-03-Glossary.md)
