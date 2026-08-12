<!--
  Page: 80 Support · Security and Privacy
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 80 Support · Security and Privacy
  Status: published (twelfth batch)
-->

# 80 Support · Security and Privacy

> **At a glance**: this page is for **managers / procurement evaluators**, answering the four things most worth confirming before letting an AI team work for you: **where data lives** (`data/` and `Logs/` under the local installation directory; the workspace directory is your choice), **what gets sent to the model** (messages, attachments, and memory projections), **Key and credential security** (kept on this machine only, clearable anytime), and **the sandbox and masking boundaries** (the AI can't get out of the workspace directory; internal identifiers are always masked). After reading this page, you should be able to answer "will data be sent away, who can see it, where is the boundary" with confidence; the operation details and mechanics are in the pages linked from each section.

**On this page**

- [Who this page is for](#who-this-page-is-for)
- [Where data lives: three directories, each with its own place](#where-data-lives-three-directories-each-with-its-own-place)
- [What gets sent to the model: messages, attachments, and memory projections](#what-gets-sent-to-the-model-messages-attachments-and-memory-projections)
- [Key and credential security: on this machine only](#key-and-credential-security-on-this-machine-only)
- [Masking: what you see is readable information](#masking-what-you-see-is-readable-information)
- [The sandbox boundary: the AI can't get out, attachments are the exception entrance](#the-sandbox-boundary-the-ai-cant-get-out-attachments-are-the-exception-entrance)
- [Common questions (for decision makers)](#common-questions-for-decision-makers)
- [Next steps](#next-steps)

---

## Who this page is for

You may not be a day-to-day Knowe operator, but someone who needs to judge "if I let an AI team work on my computer, are the data and permission boundaries controllable" — a manager, a procurement evaluator, or an IT / compliance colleague bringing Knowe in for the first time. This page organizes Knowe's data and permission boundaries into a set of "data and permission boundary" Q&As along four threads:

- **Where data lives** — what lands on this machine, what isn't inside the installation directory;
- **Who can see it** — who can access the data, what gets sent to the model, and to whom;
- **Whether anything gets uploaded** — does Knowe have a cloud, what leaves your machine;
- **Where the boundary is** — which files the AI can touch, how the interface masks.

Each conclusion points to a body page; follow the links for the mechanics and operation details when needed.

## Where data lives: three directories, each with its own place

All of Knowe's data is on this machine; keep the three directories straight (definitions in [40 Advanced · Environment Variables and Deployment Modes · The data directory: data and Logs](40-04-Environment-Variables-and-Deployment.md#the-data-directory-data-and-logs) and [60 Troubleshooting · Directory and Data Recovery · Where your data lives under the installation directory](60-03-Directory-and-Data-Recovery.md#where-your-data-lives-under-the-installation-directory)):

| Directory | What it stores | Location |
|:--|:--|:--|
| **`data/`** | App data: chat records, project data | Under the installation directory |
| **`Logs/`** | Logs | Under the installation directory |
| **Workspace directory** | Files produced by the team | The directory you chose when creating the project — **not inside the installation directory** |

For decision makers asking "what stays, what gets deleted", three key points:

- **Backup**: to back up data, back up `data/` and `Logs/` under the installation directory;
- **Uninstall**: `data/` and `Logs/` are kept by default (reinstalling can recover them); **unchecking deletes the data along with everything else, unrecoverable** (see [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention));
- **Uninstalling doesn't delete your project folder**: the workspace directory is your own choice, not inside the installation directory, and uninstalling doesn't affect the files in it (same as above).

## What gets sent to the model: messages, attachments, and memory projections

Knowe is a **locally running** app: model requests go directly from this machine to the **model provider you chose** (see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements)) — not relayed by Knowe, no Knowe cloud service involved. What gets sent to the model consists of the input you actively produce:

- **Messages** — every message you send goes out as model-call content;
- **Attachments** — files you drag into the composer yourself: images go through multimodal direct reading, the rest are packed into file content blocks and sent to the model (see [20 Guides · Files and Attachments · How the AI reads attachments](20-05-Files-and-Attachments.md#how-the-ai-reads-attachments));
- **Memory projections** — so the model "remembers the project", each reply also loads a projection of the project memory, layered on demand (definitions in [10 Core Concepts · Memory and Context · The three layers of project memory](10-05-Memory-and-Context.md#the-three-layers-of-project-memory)):

| Memory projection | How it's loaded | What it does |
|:--|:--|:--|
| **Recent activity** | **Loaded with every reply** | The latest messages and actions, accurately reconstructing "what's happening right now" |
| **Rolling summary** | **Loaded with every reply** (resident in the context) | A compressed summary of earlier activity, keeping the grip on the project's overall progress |
| **Historical activity segments** | **Read on demand** | The earlier full records, revisited only when details need checking |

In one sentence: **every reply carries the current context of "recent activity + rolling summary"; the earlier full history isn't sent out in full with every reply — it's only revisited segment by segment when needed.** This keeps the background without repeatedly sending the project's whole history to the model.

## Key and credential security: on this machine only

(Definitions in [30 Configuration · Models and Providers · API Key security: not written to disk](30-01-Models-and-Providers.md#api-key-security-not-written-to-disk))

- **Kept on this machine only** — the interface says "used to call the selected model, kept on this machine only"; Knowe only exchanges requests with the provider of the selected model, **depending on no extra account or cloud service** (see [02 Installation and System Requirements · System requirements](02-Installation-and-System-Requirements.md#system-requirements));
- **Never written to browser storage** — the API Key isn't stored in localStorage; there's no key copy in the browser storage;
- **Clearable and re-fillable anytime** — in "Settings → Models and Providers" you can clear the saved Key and re-enter it;
- **UI process isolation** — Knowe's UI process enables contextIsolation, the renderer process has no Node capability, and the UI layer can't directly access system resources.

![](docs/assets/S57-API Key 输入画面（示意）.png)

## Masking: what you see is readable information

Masking handles **internal identifiers**, so what you see is readable information rather than machine identifiers (definitions in [40 Advanced · DMs, Memory, and Permission Boundaries · What gets masked](40-03-DM-Memory-and-Permission-Boundaries.md#what-gets-masked)):

- **Member internal ids** — always masked in the interface's natural language; you see member names, not internal identifiers;
- **Internal paths** — the same rule: the interface shows readable paths; internal paths aren't exposed directly;
- **Key screens in the UI** — screens that involve Keys follow the mask / leave-empty guideline (see [Models and Providers · API Key security: not written to disk](30-01-Models-and-Providers.md#api-key-security-not-written-to-disk) and [30 Configuration · Account and Identity · About: version and build info](30-04-Account-and-Identity.md#about-version-and-build-info));
- **contextIsolation at the engineering layer** — the renderer process doesn't directly hold Node capability, so the UI layer can't bypass the app boundary and operate on machine resources directly — another boundary in Knowe's privacy design.

Note: masking targets **internal identifiers**, not your chat content — chat content is stored and used according to the project memory rules (see [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)).

## The sandbox boundary: the AI can't get out, attachments are the exception entrance

(Definitions in [10 Core Concepts · Projects and Workspaces · The workspace directory: the AI's sandbox](10-03-Projects-and-Workspaces.md#the-workspace-directory-the-ais-sandbox) and [20 Guides · Files and Attachments · The safety guard: only files you picked yourself are read](20-05-Files-and-Attachments.md#the-safety-guard-only-files-you-picked-yourself-are-read))

- The team (the Coordinator verifying, the Workers executing) reads and writes files **only within the workspace directory**; any file outside the directory is, by default, unreadable and unwritable for the AI;
- **The attachments you drag into the composer yourself are the exception entrance** — and only paths the app has "seen and signed" with its own eyes are read, so it can't be tricked into reading arbitrary files;
- In one sentence: **whatever land you fence in, the team works within that land.** Want to show the team an extra file — drag it in yourself.

## Common questions (for decision makers)

**Q: Will data be uploaded to Knowe servers?**

No. Knowe is a locally running app — the data is all on this machine (`data/` and `Logs/`), with no dependency on a Knowe cloud account or cloud service (see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements)). The only content leaving this machine is the model requests you actively make — sent directly from this machine to the model provider you chose.

**Q: Which models will my chat content be sent to?**

Only to the models configured in "Settings → Models and Providers" — the primary model, the fallback model (the automatic safety net when the primary model is unavailable), and per-member bound models (see [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)). Knowe never sends content to an unconfigured provider.

**Q: What if an API Key leaks?**

First revoke or rotate the key in the **model provider's console** so the old Key is invalidated; then go back to "Settings → Models and Providers" to clear it and re-enter the new Key. Because the Key isn't stored in localStorage and lives on this machine only, there's no residual key copy on the UI side to be read (see [API Key security: not written to disk](30-01-Models-and-Providers.md#api-key-security-not-written-to-disk)).

**Q: Which files on my machine can members touch?**

By default, only the workspace directory. The team reads and writes within this one directory — it "can't get out"; attachments are the exception entrance you personally allow, and only files whose paths the app confirmed and signed are read (see [The safety guard: only files you picked yourself are read](20-05-Files-and-Attachments.md#the-safety-guard-only-files-you-picked-yourself-are-read)). **The AI never reads arbitrary files on your machine on its own.**

**Q: Where does the data go after deleting a project / uninstalling?**

If you no longer need a project, its project records and chat records stay in the local `data/` for you to handle — archive the team and keep the directory (see [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)). Uninstalling keeps `data/` and `Logs/` by default (reinstalling can recover them); unchecking deletes the data along with everything else, **unrecoverable**; the workspace directory isn't inside the installation directory, so uninstalling doesn't affect the files in it (see [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention)).

## Next steps

- Want to review the mechanics of DMs, memory, and permission boundaries → [40 Advanced · DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md)
- Want to review the three memory layers and DM write-back → [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md)
- Data backup / a missing directory / uninstall → [60 Troubleshooting · Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)
- Have feedback to report? → [80 Support · Contact Us](80-02-Contact-Us.md)

---

**Previous**: [70-01 Changelog](70-01-Changelog.md)
**Next**: [80-02 Contact Us](80-02-Contact-Us.md)
