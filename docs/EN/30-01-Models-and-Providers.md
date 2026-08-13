<!--
  Page: 30 Configuration · Models and Providers
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 30 Configuration · Models and Providers
  Status: published (seventh batch)
-->

# 30 Configuration · Models and Providers

> **At a glance**: the model is the brain the team works with. This page covers how models are bound and switched in Knowe: the **primary model** is the default working brain (with the connection test and the mandatory first-run setup); the **fallback model** is the automatic safety net when the primary model is unavailable; **per-member model binding** lets individual members run on different models (and when it's worth it); and finally the **API Key** security notes — encrypted locally with Windows DPAPI, never written to browser storage, and clearable anytime.

**On this page**

- [Primary model: binding and the connection test](#primary-model-binding-and-the-connection-test)
- [First launch: mandatory setup](#first-launch-mandatory-setup)
- [The fallback model and automatic fallback](#the-fallback-model-and-automatic-fallback)
- [Per-member model binding: when it's worth it](#per-member-model-binding-when-its-worth-it)
- [API Key security: encrypted local storage](#api-key-security-not-written-to-disk)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Primary model: binding and the connection test

The **primary model** is the team's default: with no special setup, every member works on it — reading the project, breaking down tasks, writing code, and verifying deliverables. The vast majority of the "thinking" runs on the primary model.

In **Settings → Models and Providers**, you can change the three ingredients of the primary model at any time:

- **Provider** — Knowe ships with 20+ built-in providers (DeepSeek, Z.AI / GLM, Kimi / Moonshot, Qwen Cloud, MiniMax, StepFun, Tencent TokenHub, OpenRouter, Anthropic, OpenAI, Google AI Studio, xAI, NovitaAI, Hugging Face, and more); the full list is on [02 Installation and System Requirements · First launch: model configuration guide](02-Installation-and-System-Requirements.md#first-launch-model-configuration-guide);
- **Model** — after picking a provider, choose one from its supported model list;
- **API Key** — the key for that provider; its security boundary is below: [API Key security: encrypted local storage](#api-key-security-not-written-to-disk).

After any change, run a **connection test** first to confirm the Key and the network both work, then let the team get going.

![](docs/assets/S39-设置 → 模型与提供方.png)

**If the connection test fails**: the failure comes with a specific reason — three common kinds:

| Reason | Meaning | What to do |
|:--|:--|:--|
| **Authentication failure** | The API Key is wrong or expired | Check that the Key is complete and has no stray spaces around it; change it and test again |
| **Network unreachable** | Can't reach the vendor's API | Check your network and proxy allowance — see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements) |
| **Insufficient balance / rate limited** | Limits on the vendor side | Check the quota and rate-limit status in the vendor console |

> Detailed troubleshooting steps: [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md).

## First launch: mandatory setup

The first time you open Knowe, you must finish the primary model configuration before entering — this is the **first-run model gate**: the background is blurred and the rest of the interface is locked; only the configuration card can be operated. Until it's done, the rest of the UI stays locked.

At the top of the setup card you first pick the **interface language** (Chinese / English); below is the model configuration form with a three-step indicator:

> `1 · Choose and fill in  ›  2 · Connection test  ›  3 · Enter`

- **Step 1 · Choose and fill in** — pick a provider → pick a model → enter the API Key → click **Confirm** to seal in the configuration;
- **Step 2 · Connection test** — after sealing, the test starts automatically; you can also click **Test and Apply** to re-test manually. On success it shows a result like "Connection passed, applying settings · 320ms";
- **Step 3 · Enter** — the **Enter Knowe** button lights up; click it to reach the main interface. The setup flow exits and the UI unlocks.

> The complete interface walkthrough is on [02 Installation and System Requirements · First launch: model configuration guide](02-Installation-and-System-Requirements.md#first-launch-model-configuration-guide); [01 Quickstart · Before you begin](01-Quickstart.md#before-you-begin) also requires completing this step first. What the first-run setup actually completes is the **primary model binding** — it's the same configuration as this page's settings entry. Afterwards you just change it in Settings → Models and Providers, no need to go through the flow again.

## The fallback model and automatic fallback

Beyond the primary model, Knowe supports configuring a **fallback model** as a safety net: when the primary model is temporarily unavailable (vendor-side rate limiting, network hiccups, an expired Key), Knowe **automatically falls back** to the fallback model so the team keeps working instead of stalling on a primary-model failure.

Key points:

- The fallback model is configured in **Settings → Models and Providers**, side by side with the primary model, and needs the same things: a provider, a model, and a usable API Key;
- Day-to-day work still runs on the primary model; the fallback only takes over when the primary is unavailable;
- For reliability-sensitive setups, consider a fallback model from a **different provider** than the primary one — spreading out the impact of a single-provider failure.

## Per-member model binding: when it's worth it

By default, the whole team shares the primary model. You can also bind an independent model to **specific members**: set it per member in **Settings → Models and Providers**. From then on, that member works on the bound model while everyone else stays on the primary model.

When is it worth it? Two most practical cases:

- **Cost tiering** — high-value tasks get the good model, low-value tasks get the cheaper option. Combined with the "by member" tab on [20 Guides · Token Usage and Cost · The breakdown table: by model / by member](20-09-Token-Usage-and-Cost.md#the-breakdown-table-by-model--by-member), first locate the cost drivers (which member, running on which model), then decide who gets a more suitable model;
- **Play to strengths** — different models have different strengths across task types; bind a certain role type to the model it's better at, so people and models are each used where they shine.

> **Note**: per-member model binding only changes which model runs behind that member — it doesn't change the approval and acceptance flow. Assigning, verifying, and reporting work exactly as before (see [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)).

<a id="api-key-security-not-written-to-disk"></a>

## API Key security: encrypted local storage

- **Kept on this machine only** — the in-app note reads "used to call the selected model, kept on this machine only". Knowe only exchanges requests with the provider of the selected model and depends on no extra account or cloud service (see [02 Installation and System Requirements · System requirements](02-Installation-and-System-Requirements.md#system-requirements));
- **Encrypted at rest by the OS** — settings keys use current-user Windows DPAPI. A DPAPI-protected digest also authenticates the complete settings projection, so offline changes to the provider URL or ciphertext are rejected. If neither the primary file nor encrypted backup validates, settings fail closed and the originals are not overwritten with empty defaults;
- **Not written to browser storage** — the API Key is never stored in `localStorage`; your key isn't in the browser storage at all;
- **Not passed to agent terminals** — model-authored shell/Python processes receive a minimal environment and do not inherit model keys, GitHub/npm tokens, or other host credentials;
- **Clearable anytime** — you can clear the saved Key in Settings → Models and Providers;
- **UI process isolation** — Knowe's UI process enables `contextIsolation` and the renderer has no Node capability, so the UI layer can't directly access system resources;
- **Masked in the interface** — internal member ids and internal paths are always masked in the UI's natural language; screens involving a Key follow the same mask/leave-blank rule (see the screenshot guidelines on this page).

> **Protection boundary**: DPAPI prevents a copied settings file from directly revealing the Key. It does not protect against malware already running as the same Windows user, nor erase historical files or system backups left by older versions. After a successful upgrade from a plaintext-storage release, rotate high-value keys at the provider.

## Common questions

**Q: Can the first-launch setup be skipped?**
No. The first-run model gate is the mandatory entry — until it's done, the rest of the UI stays locked (see [02 Installation and System Requirements · First launch: model configuration guide](02-Installation-and-System-Requirements.md#first-launch-model-configuration-guide)).

**Q: Do I need to redo the first-launch setup to switch the primary model?**
No. The gate only appears on the very first launch; afterwards, change the provider, model, or Key directly in Settings → Models and Providers and run a connection test.

**Q: Do all members use the same model?**
By default, yes. You can bind individual members to independent models so specific members run on a different one.

**Q: Where is the API Key stored? Could it be sent somewhere?**
The Key stays on this machine as Windows DPAPI ciphertext, is never written to browser storage, and can be cleared anytime in Settings. Knowe only exchanges requests with the provider of the selected model — no extra accounts or cloud services (see [02 Installation and System Requirements · System requirements](02-Installation-and-System-Requirements.md#system-requirements)).

**Q: What if the primary model is unavailable?**
If a fallback model is configured, Knowe automatically falls back when the primary is unavailable. If not, first troubleshoot in Settings → Models and Providers (the three causes above: authentication failure / network unreachable / insufficient balance or rate limited); detailed steps are on [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md).

## Next steps

- Want to adjust the approval rhythm and how notifications reach you? → [30 Configuration · Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md)
- Want to check costs by member and model? → [20 Guides · Token Usage and Cost](20-09-Token-Usage-and-Cost.md)
- Want to review the full first-configuration steps? → [02 Installation and System Requirements · First launch: model configuration guide](02-Installation-and-System-Requirements.md#first-launch-model-configuration-guide)

---

**Previous**: [20-09 Token Usage and Cost](20-09-Token-Usage-and-Cost.md)
**Next**: [30-02 Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md)
