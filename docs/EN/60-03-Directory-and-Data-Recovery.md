<!--
  Page: 60 Troubleshooting · Directory and Data Recovery
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 60 Troubleshooting · Directory and Data Recovery
  Status: published (twelfth batch)
-->

# 60 Troubleshooting · Directory and Data Recovery

> **At a glance**: a project = a group chat + one local **workspace directory**. When you move, rename, or delete the project folder in File Explorer, the project "can't find" its directory — that's a **Missing directory** state. This page covers: how to use the **Recovery card** (a 5-minute countdown) that appears in the chat stream when the directory goes missing, which situations the two recovery paths — **rename it / pick a new directory** — fit, and where Knowe's data lives under the installation directory (`data/` and `Logs/`), useful for backup and uninstall.

**On this page**

- [Symptom: the project directory is missing](#symptom-the-project-directory-is-missing)
- [The recovery card: a 5-minute countdown](#the-recovery-card-a-5-minute-countdown)
- [Path 1: rename it (the directory is still there, the path changed)](#path-1-rename-it-the-directory-is-still-there-the-path-changed)
- [Path 2: pick a new directory (the directory is really gone)](#path-2-pick-a-new-directory-the-directory-is-really-gone)
- [After the recovery card expires](#after-the-recovery-card-expires)
- [Where your data lives under the installation directory](#where-your-data-lives-under-the-installation-directory)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Symptom: the project directory is missing

**Symptom**: you moved, renamed, or deleted the project folder in File Explorer; back in Knowe, a **Recovery card** appears in the project's chat stream (see [10 Core Concepts · Projects and Workspaces · Recovery when the directory fails](10-03-Projects-and-Workspaces.md#recovery-when-the-directory-fails)).

**Background**: a project is bound to a **directory path** — once the directory is moved or renamed, the project can't find it anymore: that's a **Missing directory** state (term definition in [50 Reference · Glossary · Projects and workspaces](50-03-Glossary.md#projects-and-workspaces)).

**Important**: **deleting the directory ≠ deleting the project** — the project records and chat records are still in the local data, just temporarily without a file landing spot.

## The recovery card: a 5-minute countdown

- The Recovery card appears **in the chat stream** with a **5-minute countdown**, giving you two choices: **rename it** or **pick a new directory** (see [Recovery when the directory fails](10-03-Projects-and-Workspaces.md#recovery-when-the-directory-fails));
- If the countdown ends without action, the card expires — but you can still **start a recovery on your own** (see [After the recovery card expires](#after-the-recovery-card-expires) below).

## Path 1: rename it (the directory is still there, the path changed)

**When it fits**: the directory was **moved / renamed** — the folder is still on disk, only the path changed.

**What to do**: on the Recovery card, choose "**Rename**" to update the project record to the directory's **new path**.

**What it does**: renaming only updates the path binding and touches no files; after it's done, the project recovers and the team keeps reading and writing in the original directory as before (the binding relationship in [10 Core Concepts · Projects and Workspaces · Project name and directory binding](10-03-Projects-and-Workspaces.md#project-name-and-directory-binding)).

## Path 2: pick a new directory (the directory is really gone)

**When it fits**: the directory is **really gone** (deleted).

**What to do**: on the Recovery card, choose "**Pick a new directory**" to give the project a different workspace directory.

**What it does**: if files from the old directory are still on disk (moved but not deleted, for example), find the directory in File Explorer and move its files into the new one before you pick; after picking, the new directory becomes the team's read/write scope (the sandbox definition in [10 Core Concepts · Projects and Workspaces · The workspace directory: the AI's sandbox](10-03-Projects-and-Workspaces.md#the-workspace-directory-the-ais-sandbox)).

## After the recovery card expires

If the countdown ends without action, the card expires — but that only means "this card is no longer counting down", and it **doesn't affect the recovery itself**: you can still start a recovery on your own, with the same operations as on the card (rename it or pick a new directory, see [Recovery when the directory fails](10-03-Projects-and-Workspaces.md#recovery-when-the-directory-fails)). The exact entry follows the product.

## Where your data lives under the installation directory

All of Knowe's data is on this machine; keep these two directories straight (see [40 Advanced · Environment Variables and Deployment Modes · The data directory: data and Logs](40-04-Environment-Variables-and-Deployment.md#the-data-directory-data-and-logs)):

| Directory | What it stores | Location |
|:--|:--|:--|
| **`data/`** | App data: chat records, project data | Under the installation directory |
| **`Logs/`** | Logs | Under the installation directory |
| **Workspace directory** | Files produced by the team | The directory you chose when creating the project — **not inside the installation directory** |

Key points:

- **Backup**: to back up data, back up `data/` and `Logs/` under the installation directory;
- **Uninstall**: `data/` and `Logs/` are kept by default (reinstalling can recover them); unchecking deletes the data along with everything else, **unrecoverable** (see [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention));
- **Uninstalling doesn't delete your project folder**: the workspace directory is one you chose yourself, not inside the installation directory, and uninstalling doesn't affect the files in it (see [Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention)).

## Common questions

**Q: The directory was deleted — can the files inside still be recovered?**

It depends on whether the files themselves have a backup (the Recycle Bin, a cloud / sync drive, and so on). Knowe handles the recovery of the "path binding"; it doesn't retrieve deleted files for you. But **deleting the directory ≠ deleting the project** — the project records and chat records are still in the local data (see [Recovery when the directory fails](10-03-Projects-and-Workspaces.md#recovery-when-the-directory-fails)).

**Q: What's the difference between renaming and picking a new directory?**

Renaming is for when the directory was moved / renamed (the path updates, files stay put); picking a new directory is for when the directory is really gone (switch to a different workspace directory) (see [Recovery when the directory fails](10-03-Projects-and-Workspaces.md#recovery-when-the-directory-fails)).

**Q: Can the project name be changed? What about the workspace directory?**

The project name can be changed anytime (it changes the group chat's name); the directory's path binding is set when the project is created, and switching the directory afterwards belongs to the "Missing directory" recovery scenario (see [Project name and directory binding](10-03-Projects-and-Workspaces.md#project-name-and-directory-binding)).

**Q: What should I back up?**

`data/` and `Logs/` under the installation directory; workspace files are in the directory you chose, and you can back them up separately (see [The data directory: data and Logs](40-04-Environment-Variables-and-Deployment.md#the-data-directory-data-and-logs)).

## Next steps

- The directory is recovered — continue the main loop → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- Want to understand why projects bind to directories → [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)
- Connection or model problems still unresolved? → [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md) / [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md)

---

**Previous**: [60-02 Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md)
**Next**: [70-01 Changelog](70-01-Changelog.md)
