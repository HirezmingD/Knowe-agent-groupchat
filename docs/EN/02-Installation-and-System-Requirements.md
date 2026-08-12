<!--
  Page: 02 Installation and System Requirements
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 02 Installation and System Requirements
  Status: published (second batch)
-->

# 02 Installation and System Requirements

> **At a glance**: this page covers the whole journey from downloading the installer to entering Knowe for the first time: system requirements, installation steps (per-user, changeable directory, optional shortcuts), the first-launch model configuration guide (provider / model / API Key / connection test), network requirements, and the data retention rules when you uninstall. The installer bundles the Python runtime and Chromium, so you don't need to install any extra dependencies.

**On this page**

- [System requirements](#system-requirements)
- [Download the installer](#download-the-installer)
- [Installation steps](#installation-steps)
- [First launch: model configuration guide](#first-launch-model-configuration-guide)
- [Network requirements](#network-requirements)
- [Uninstalling and data retention](#uninstalling-and-data-retention)
- [Related links](#related-links)

---

## System requirements

| Item | Requirement |
|:--|:--|
| Operating system | Windows **x64 (64-bit)**; the installer is only provided for the x64 architecture |
| Disk space | About 700 MB–1 GB after installation (the app itself + the bundled backend runtime + the headless browser); 2 GB of free space recommended |
| Memory | 8 GB or more recommended (no hard minimum; the bigger the team and the heavier the tasks, the higher the usage) |
| Network | You must be able to reach the model provider's API you chose (HTTPS outbound) — see [Network requirements](#network-requirements) |
| Nothing else to install | Python, Node.js, Chrome / Edge and so on are **not** needed — the installer bundles the Python runtime and Chromium (Playwright) |

> **Note**: Knowe is a locally running app: it ships with its own backend process, and the window has a connection status badge (Connecting / Connected / Reconnecting / Disconnected, and other states). It only exchanges requests with the model provider you chose — it doesn't depend on any extra account or cloud service.

## Download the installer

Download the installer from the official website or a release channel. The file is named:

```
Knowe Setup 1.0.25.2.exe
```

## Installation steps

Installation is **wizard-driven** (not a one-click silent install) and needs **no administrator rights** at any point (per-user install; nothing is written to system directories). Double-click the installer and follow the wizard:

**Step 1 · Welcome page**: confirm installing Knowe and click "Next".

![](docs/assets/S08-安装器欢迎页.png)

**Step 2 · Choose the install directory**: by default it installs into the **current user's** program directory (a location writable without administrator rights); you can click "Browse" to pick another directory. **Remember this path** — app data is stored there by default (see [Uninstalling and data retention](#uninstalling-and-data-retention)).

![](docs/assets/S09-选择安装目录页.png)

**Step 3 · Shortcuts**: choose which shortcuts to create; both options are **checked by default** — uncheck as needed:

- Create a desktop shortcut
- Create a Start Menu shortcut

![](docs/assets/S10-快捷方式选择页.png)

**Step 4 · Install and finish**: click "Install" and wait for the progress bar to complete; on the finish page you can check "Run Knowe" (checked by default) and click "Finish".

![](docs/assets/S11-安装完成页.png)

After installation, a **Knowe** shortcut appears on the desktop / in the Start Menu. Double-click it to enter the [First launch: model configuration guide](#first-launch-model-configuration-guide).

> **Install troubleshooting**: if the installer won't start or reports corruption, delete it and download it again (the installer is not code-signed; Windows SmartScreen may show "Windows protected your PC" — this is the standard prompt for unsigned software, click "More info → Run anyway" to continue). See the 60 Troubleshooting volume ([Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md) · [Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md) · [Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)).

## First launch: model configuration guide

**The first time you open Knowe, you must complete the model configuration before you can enter** — this is a forced guide: the background is blurred and every other UI element is locked; only the configuration card is operable. The reason is simple: your AI team needs a large model to do any work, and without a usable model there's nothing to do in the main interface.

At the top of the guide card you first choose the **interface language** (Chinese / English); below it is the model configuration form, with a three-step indicator at the top:

> `1 · Choose and fill in  ›  2 · Connection test  ›  3 · Enter`

**Step 1 · Choose and fill in**

1. **Model provider**: pick a vendor from the dropdown. Knowe ships with 20+ providers built in, for example DeepSeek, Z.AI / GLM, Kimi / Moonshot, Qwen Cloud, MiniMax, StepFun, Tencent TokenHub, OpenRouter, Anthropic, OpenAI, Google AI Studio, xAI, NovitaAI, Hugging Face, and more.
2. **Model**: after choosing a provider, pick one model from its list of supported models.
3. **API Key**: paste the API Key for that vendor. The Key is **kept on this machine only** (the UI note reads "used to call the selected model, kept on this machine only") and is never written to browser storage; you can clear the saved Key at any time in Settings → Models and Providers.
4. Click "**OK**" to seal in the configuration (until a model is selected, "OK" is disabled and the card prompts "pick a model first, then continue").

**Step 2 · Connection test**

Once the configuration is sealed in, Knowe **starts a connection test automatically**; you can also click "**Test and Apply**" to test again manually. When the test passes, the card shows a result like "Connection passed, applying settings · 320ms" and moves on to the next step.

**Step 3 · Enter**

When everything is ready, the "**Enter Knowe**" button lights up. Click it to enter the main interface and see Zinnia's welcome message. The guide exits and the rest of the interface unlocks.

![](docs/assets/S12-首次启动模型配置引导卡片.png)

**When the test fails**, the card gives the specific reason. The three common kinds: **authentication failure** (wrong or expired Key), **network unreachable** (can't reach the vendor's API — see [Network requirements](#network-requirements)), **insufficient balance / rate limited** (limits on the vendor side). After fixing it, click "Test and Apply" again. See [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md).

> **Note**: after the first configuration, you can change the provider, model, or Key at any time in Settings → Models and Providers, and you can bind an independent model to individual members (see [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)).

## Network requirements

Knowe's network needs are clear — there's one main line:

- **You must be able to reach the model provider's API you chose** (HTTPS outbound). Domains differ by vendor — DeepSeek, for example, is `api.deepseek.com`; whichever one you pick in the guide card is the one you need to reach.
- The app's internal backend runs on **this machine**, using WS port 8080 / HTTP port 8081 by default; if a port is taken it automatically avoids the collision by moving to another port, so you usually don't need to handle it manually.
- On a corporate network / proxy environment, allow HTTPS outbound to the model provider; a fully internal network usually can't pass the connection test.

> **Quick self-check**: when the connection test fails, check in order — ① whether the API Key is complete with no leading or trailing spaces; ② whether this machine can reach the vendor's website / API domain; ③ whether a corporate proxy is blocking it. The first two cover the vast majority of cases.

## Uninstalling and data retention

Uninstall Knowe via Settings → Account and Identity → About, or via Windows Settings → Apps. The uninstaller asks one question first:

> **Keep chat history and project data** (checked by default): reinstalling after an uninstall restores them

- **Checked (default)**: uninstalling **only deletes the program files**; your chat history, project data, and logs stay under `data/` and `Logs/` in the install directory, and the data is still there after you reinstall.
- **Unchecked**: **full uninstall** — the data is deleted along with everything else. This step **cannot be undone**; make sure you no longer need the data before you do it.

Two more things to know:

- Uninstalling does **not delete** project folders on your disk — the project workspace directory is one you chose yourself (see [Quickstart · Step 2](01-Quickstart.md#step-2--confirm-the-create-project-card-project-name-and-workspace-directory)); it isn't inside the install directory, so uninstalling doesn't touch the files in it.
- The `data/` folder in the install directory holds app data (including chat history and project data), and `Logs/` holds logs; to back up your data, back up these two directories.

![](docs/assets/S13-卸载器「保留数据」页.png)

## Related links

- Run your first project → [01 Quickstart](01-Quickstart.md)
- What the product is and what it solves → [00 Overview](00-Overview.md)
- Connection test fails / backend status abnormal → [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md) / [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md)
- Model configuration in depth (per-member model binding, fallback model) → [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)

---

**Previous**: [01 Quickstart](01-Quickstart.md)
**Next**: [10-01 Lead a Team by Chat](10-01-Lead-a-Team-by-Chat.md)
