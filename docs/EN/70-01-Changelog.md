<!--
  Page: 70 Releases · Changelog
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 70 Releases · Changelog
  Status: published (twelfth batch)
-->

# 70 Releases · Changelog

> **At a glance**: this page records Knowe updates in reverse version order — the current version **v1.0.25** is the main entry, labeled by **New features / Fixes / Behavior changes**; records for earlier versions (v1.0.24 and before) follow the product's actual state and are supplemented at the documentation's release pace. Want to know "should I upgrade", "where do I see the version number", "how often is the changelog updated" — see [Common questions](#common-questions) on this page; want to pick your focus by role — see [What this version means for you](#what-this-version-means-for-you).

**On this page**

- [Current version: v1.0.25](#current-version-v1025)
- [Earlier versions (v1.0.24 and before)](#earlier-versions-v1024-and-before)
- [What this version means for you](#what-this-version-means-for-you)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Current version: v1.0.25

> **Note**: this page is the changelog published with the documentation for the first time; the v1.0.25 entry covers the **feature surface of the current version**. Version numbering follows the whole documentation (the v1.0.25.x series — see [30 Configuration · Account and Identity · About: version and build info](30-04-Account-and-Identity.md#about-version-and-build-info)); the entries below are labeled by **New features / Fixes / Behavior changes**.

**New features** (this version's feature surface; the entries are taken from the features covered by the already published documentation — details in each linked page):

- **Install and configure** — Windows x64 per-user install, the installer bundles its own runtime, and the first-launch model configuration guide (pick a provider → pick a model → fill in the API Key → the connection test must pass before you enter) (see [02 Installation and System Requirements](02-Installation-and-System-Requirements.md), [30 Configuration · Models and Providers](30-01-Models-and-Providers.md));
- **Projects and the team** — a project = a group chat + a local workspace directory; the Coordinator breaks tasks down; 24 worker roles, each with the "Good at" and "Not suitable for" boundaries; adding people / assigning tasks / removing members all wait for your confirmation on an Approval Card (see [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md), [10 Core Concepts · Approval Mechanism](10-04-Approval-Mechanism.md), [20 Guides · Create a Project and Build a Team](20-01-Create-Project-and-Build-Team.md), [50 Reference · Roles Catalog](50-01-Roles-Catalog.md));
- **Assign and accept** — the Task card (who / the task / the Coordinator's note and feedback history), members working with streaming output, the Coordinator verifying the deliverable by reading the files and then reporting to you (see [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md));
- **Communication** — group chat and in-project DMs, @mention, Quote / Forward / Favorite, drafts saved per conversation (see [20 Guides · Group Chat and DMs](20-04-Group-Chat-and-DM.md));
- **Files** — attachments (images read directly via multimodality, the rest packed into file content blocks), a separate preview window rendering multiple formats (see [20 Guides · Files and Attachments](20-05-Files-and-Attachments.md), [20 Guides · File Preview Window](20-08-File-Preview-Window.md));
- **Knowledge and memory** — knowledge assets (two scopes: global / project), three types of skill packs, the three-layer project memory, DM content written back to project memory (see [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md), [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md), [40 Advanced · Skill Pack Management](40-02-Skill-Pack-Management.md), [10 Core Concepts · Memory and Context](10-05-Memory-and-Context.md));
- **Search and cost** — global search, Favorites, contact profile pages; the Token usage dashboard (filter by date range, the breakdown by model / by member, amounts in RMB (¥)) (see [20 Guides · Search, Favorites, and Contacts](20-07-Search-Favorites-and-Contacts.md), [20 Guides · Token Usage and Cost](20-09-Token-Usage-and-Cost.md));
- **Configuration and security** — models and providers (primary model binding and the connection test, the fallback model, per-member model binding, the approval timeout), notifications, appearance, account and identity; the signed-path guard for attachments, the API Key never written to browser storage, internal identifiers masked, sandbox boundaries (see [30 Configuration · Models and Providers](30-01-Models-and-Providers.md), [80 Support · Security and Privacy](80-01-Security-and-Privacy.md));
- **Troubleshooting and maintenance** — the connection status badge (six states), the backend auto-restarting after a crash, the recovery card for a missing directory (see [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md), [60 Troubleshooting · Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)).

**Fixes** — this page doesn't invent fix entries: the specific fix records (the concrete errors and numbers involved) follow the product's actual release notes; from this page's publication onward, fix entries are added alongside each release.

**Behavior changes** — likewise follow the product: interaction or terminology changes are flagged on the corresponding feature pages (for example [30 Configuration · Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md), [Account and Identity](30-04-Account-and-Identity.md)), and this page doesn't fabricate cross-version behavior differences.

![](docs/assets/S44-关于页.png)

## Earlier versions (v1.0.24 and before)

Update records for earlier versions follow the **product's actual state** and are supplemented on this page at the documentation's release pace. To avoid fabricating historical entries, this page doesn't list specific version entries for v1.0.24 and before. If your version is lower than v1.0.25, first confirm this version's feature surface via [01 Quickstart](01-Quickstart.md), or use [80 Support · Contact Us](80-02-Contact-Us.md) to request the missing history.

## What this version means for you

- **New users**: this version's feature surface fully covers the main loop of [Quickstart](01-Quickstart.md) — create a project → build the team → assign a task → receive the deliverable; walking through [Quickstart](01-Quickstart.md) once confirms whether this version is enough for you;
- **Daily users**: the features you use every day are all within this version's feature surface — [Assign and Accept](20-03-Assign-and-Accept.md), [Group Chat and DMs](20-04-Group-Chat-and-DM.md), [Files and Attachments](20-05-Files-and-Attachments.md), [Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md); consult them as needed;
- **Managers**: two pages matter most for this version's decisions — [Approval Mechanism: The Boundary Between People and AI](10-04-Approval-Mechanism.md) (every team action waits for your confirmation on an Approval Card) and [Security and Privacy](80-01-Security-and-Privacy.md) (data on this machine, where the sandbox boundary is).

## Common questions

**Q: How do I know whether I should upgrade?**

First check whether this version's [New features](#current-version-v1025) has what you need, then compare your current version with the target version number in "Settings → Account and Identity → About" (see [About: version and build info](30-04-Account-and-Identity.md#about-version-and-build-info)); this page updates alongside releases — check the latest entry before upgrading.

**Q: Where do I see my version number?**

"Settings → Account and Identity → About" — the version number and build info are both there (see [About: version and build info](30-04-Account-and-Identity.md#about-version-and-build-info)).

**Q: How often is the changelog updated?**

Maintained at the release pace: when a new version is released, this page adds the corresponding entry; records for earlier versions (v1.0.24 and before) follow the product's actual state and are supplemented at the documentation's release pace.

## Next steps

- Haven't run through the main loop yet? → [01 Quickstart](01-Quickstart.md)
- Want reading paths by role? → [00 Overview · Documentation map and reading paths](00-Overview.md#documentation-map-and-reading-paths)
- Have feedback to report? → [80 Support · Contact Us](80-02-Contact-Us.md)

---

**Previous**: [60-03 Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)
**Next**: [80-01 Security and Privacy](80-01-Security-and-Privacy.md)
