<!--
  Page: 30 Configuration · Account and Identity
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 30 Configuration · Account and Identity
  Status: published (eighth batch)
-->

# 30 Configuration · Account and Identity

> **At a glance**: this page covers your "personal profile" and Knowe's "identity info": set your **display name** and upload an **avatar** in Settings → Account and Identity; and the **About page** — check the version number and build info. The About page is also the **uninstall entry point** (the same entry and the same policy as [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention): uninstalling keeps chat history and project data by default).

**On this page**

- [Settings → Account and Identity](#settings--account-and-identity)
- [Display name](#display-name)
- [Avatar upload](#avatar-upload)
- [About: version and build info](#about-version-and-build-info)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Settings → Account and Identity

"Account and Identity" is a section of Settings that handles two things: who you are — your **display name** and **avatar**; and who Knowe itself is — **About** (the version number and build info).

## Display name

In Settings → Account and Identity you can set your **display name** — it's your identity marker in Knowe, and the team (the Coordinator, Workers) knows you by this name. You can change it anytime.

## Avatar upload

In Settings → Account and Identity you can **upload an avatar** to replace the default one.

![](docs/assets/S43-账户与身份设置.png)

## About: version and build info

"Settings → Account and Identity → About" shows Knowe's identity info:

- **Version number** — the currently installed version (the v1.0.25.x series; the installer is named like `Knowe Setup 1.0.25.2.exe`, see [02 Installation and System Requirements](02-Installation-and-System-Requirements.md));
- **Build info** — the build number, build time, and more (follow what the page actually shows).

**The About page is also the uninstall entry**: you can uninstall Knowe via Settings → Account and Identity → About, or via Windows Settings → Apps. The uninstaller asks one question first (see [02 Installation and System Requirements · Uninstalling and data retention](02-Installation-and-System-Requirements.md#uninstalling-and-data-retention)):

> **Keep chat history and project data** (checked by default): reinstalling after an uninstall restores them

- **Checked (default)** — uninstalling only deletes the program files; chat history, project data, and logs stay under `data/` and `Logs/` in the install directory, and the data is still there after you reinstall;
- **Unchecked** — a full uninstall: the data is deleted along with everything else, and this **cannot be undone**.

Two more things to know:

- Uninstalling does **not delete** the project folders on your disk — the workspace directory is one you chose yourself and isn't inside the install directory, so uninstalling doesn't affect the files in it;
- To back up your data, back up `data/` and `Logs/` in the install directory.

![](docs/assets/S44-关于页.png)

## Common questions

**Q: Where can I find the version number?**
Settings → Account and Identity → About.

**Q: Will uninstalling delete my project files?**
No. The workspace directory is one you chose yourself and isn't inside the install directory; uninstalling also keeps chat history and project data by default (restorable after reinstalling).

**Q: Does changing my display name and avatar affect the team?**
No. It only updates your own identity info — it doesn't affect the team's composition or project data.

**Q: Is my data still there after uninstalling and reinstalling?**
If "Keep chat history and project data" is checked (the default) when you uninstall, the data is still there after reinstalling; unchecking it means a full uninstall that cannot be undone.

## Next steps

- You've finished 30 Configuration — next is [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md)
- Want to review the full install, first launch, and uninstall flow? → [02 Installation and System Requirements](02-Installation-and-System-Requirements.md)
- Want to adjust the appearance and interface language? → [30 Configuration · Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md)
- Want to switch models, configure a fallback model, or bind per member? → [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)

---

**Previous**: [30-03 Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md)
**Next**: [40-01 Knowledge Curation](40-01-Knowledge-Curation.md)
