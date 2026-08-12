<!--
  Page: 40 Advanced · Environment Variables and Deployment Modes
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 40 Advanced · Environment Variables and Deployment Modes
  Status: published (ninth batch)
-->

# 40 Advanced · Environment Variables and Deployment Modes

> **At a glance**: this page is for **advanced users and special environments** (corporate networks, port conflicts, data backup). It explains Knowe's deployment modes: the app's bundled backend process and local ports (default WS 8080 / HTTP 8081, auto-avoiding when occupied), the data directory (`data/` and `Logs/` under the installation directory), and the **categories and uses of `KNOWE_*` environment variables**. **Most users don't need this page and don't need to set any environment variables** — the default configuration works out of the box; this page is mainly prepared for troubleshooting and tuning. Exact variable names are filled in with each version and follow the product — this page doesn't invent any.

**On this page**

- [Why environment variables exist](#why-environment-variables-exist)
- [Ports and the backend: the local running mode](#ports-and-the-backend-the-local-running-mode)
- [The data directory: data and Logs](#the-data-directory-data-and-logs)
- [KNOWE_* environment variables: categories and uses](#knowe_-environment-variables-categories-and-uses)
- [Tuning options for advanced users](#tuning-options-for-advanced-users)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Why environment variables exist

Knowe runs **locally**: the app bundles its own backend process, and model requests go from this machine directly to the model provider (see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements)) — no extra accounts or cloud services. In this local architecture, **environment variables** are "backend switches you can change without opening the UI" — for advanced users to adjust runtime behavior in special environments, for example:

- connectivity tuning in corporate network / proxy environments;
- handling when a port is occupied;
- backing up and migrating the data directory.

**Naming convention**: Knowe's related environment variables carry the **`KNOWE_`** prefix and are organized by category (backend / terminal / network / browser / memory / skills switches). Exact variable names, values, and defaults are reference content shipped with each version — **this page only explains categories and uses, and doesn't invent variable names**.

## Ports and the backend: the local running mode

- The app's bundled backend process runs on **this machine**, using the **WS 8080 / HTTP 8081** ports by default (see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements));
- When a port is occupied, it **auto-avoids** to another port — generally no manual handling needed;
- The window has a **connection status badge** (Connecting / Connected / Reconnecting / Disconnected, and other states); when the backend is abnormal, you can restart it via "Retry" (detailed troubleshooting: [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md)).

## The data directory: data and Logs

All of Knowe's data is on this machine — keep these two directories straight:

| Directory | What it stores | Location |
|:--|:--|:--|
| **`data/`** | App data: chat records, project data | Under the installation directory (see [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention)) |
| **`Logs/`** | Logs | Under the installation directory (same as above) |
| **Workspace directory** | Files produced by the team | A local directory you choose when creating the project — **not inside the installation directory** (see [10 Core Concepts · Projects and Workspaces](10-03-Projects-and-Workspaces.md)) |

Key points:

- **Backup**: to back up data, back up `data/` and `Logs/` under the installation directory — the same line as [30 Configuration · Account and Identity · About: version and build info](30-04-Account-and-Identity.md#about-version-and-build-info);
- **Uninstall**: `data/` and `Logs/` are kept by default (reinstalling can recover them); unchecking deletes the data along with everything else, unrecoverable (see [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention)).

## KNOWE_* environment variables: categories and uses

`KNOWE_*` environment variables provide switches and tuning options by category, per the planned scope:

| Category | Use (planned scope) | Exact variable names |
|:--|:--|:--|
| **Backend** | Switches for running the backend process and its ports | Per the product, filled in with each version |
| **Terminal** | Switches for terminal-related behavior | Per the product, filled in with each version |
| **Network** | Network and proxy tuning | Per the product, filled in with each version |
| **Browser** | Settings for the built-in browser (Chromium / Playwright) | Per the product, filled in with each version |
| **Memory** | Switches for memory and context | Per the product, filled in with each version |
| **Skills** | Switches related to skills (SKILL) | Per the product, filled in with each version |

> **Important**: the table above lists **categories and uses**, not exact variable names. Exact variable names, values, and defaults ship with each version and follow the product; this page doesn't invent names. If you need them, follow the in-app instructions or the reference content shipped with the version.

## Tuning options for advanced users

Based on what this page has covered, the main things advanced users can tune are:

- **Ports**: default WS 8080 / HTTP 8081, auto-avoiding when occupied — generally nothing to handle; when troubleshooting, check whether other programs on this machine are using these two ports (detailed troubleshooting: [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md));
- **Network / proxy**: corporate networks or proxy environments need outbound HTTPS to the model provider allowed ([02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements));
- **Data backup / migration**: back up `data/` and `Logs/` under the installation directory; the workspace directory is your own choice and accessible anytime in File Explorer;
- **Resource headroom**: the system requirements recommend reserving about 2 GB of free disk space and 8 GB of memory (the bigger the team and the heavier the tasks, the higher the usage — see [02 Installation and System Requirements · System requirements](02-Installation-and-System-Requirements.md#system-requirements)).

## Common questions

**Q: Do I need to set environment variables?**
Most users don't — the default configuration works fine. Environment variables mainly serve advanced users tuning in special environments (proxy, port conflicts, data backup).

**Q: What happens if a port is taken?**
The backend auto-avoids to another port — generally no manual handling; when troubleshooting, check whether any program on this machine is using 8080 / 8081.

**Q: Where is my data stored, and how do I back it up?**
`data/` (app data) and `Logs/` (logs) under the installation directory — back these two up; the workspace directory is a self-chosen output location and isn't inside the installation directory.

**Q: Why doesn't this page list exact variable names?**
Exact variable names, values, and defaults ship with each version and follow the product — this page only explains categories and uses, and doesn't invent any.

## Next steps

- You've finished 40 Advanced — next is [50 Reference · Roles Catalog](50-01-Roles-Catalog.md)
- Want to review installation, uninstall, and data retention? → [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)
- Want to troubleshoot backend / connection issues? → [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md)

---

**Previous**: [40-03 DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md)
**Next**: [50-01 Roles Catalog](50-01-Roles-Catalog.md)
