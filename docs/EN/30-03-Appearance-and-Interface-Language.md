<!--
  Page: 30 Configuration · Appearance and Interface Language
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 30 Configuration · Appearance and Interface Language
  Status: published (seventh batch)
-->

# 30 Configuration · Appearance and Interface Language

> **At a glance**: this page covers how Knowe looks and which language it speaks: the **dark / light theme** (defaults to following the system) and **large text mode**; instant switching of the **interface language** between Chinese / English; and one localization rule — **role names translate with the language; member names stay as-is**.

**On this page**

- [Dark / light: follow the system](#dark--light-follow-the-system)
- [Large text mode](#large-text-mode)
- [Interface language: instant switching](#interface-language-instant-switching)
- [Role names follow the language; member names don't translate](#role-names-follow-the-language-member-names-dont-translate)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Dark / light: follow the system

Knowe's theme **defaults to following the system appearance**: if the system is dark, Knowe is dark; if the system is light, Knowe is light — the overall look stays consistent with the system.

- Dark and light are each a complete theme: the chat stream, Approval Cards, the roster, and the Settings window all switch together;
- Screenshots in the documentation are all in the light theme.

![](docs/assets/S41-深色与浅色主题对比（左右两张并排）.png)

## Large text mode

- The **large text mode** toggle is in Settings: when on, the UI text scales up overall for easier reading;
- It suits large displays, higher resolutions, or long sessions staring at the screen.

## Interface language: instant switching

- The interface language supports **Chinese / English**, switched in Settings;
- Switching takes effect **instantly** — no restart needed; the UI text changes right away;
- The first-launch setup card also asks you to pick the interface language first (see [02 Installation and System Requirements · First launch: model configuration guide](02-Installation-and-System-Requirements.md#first-launch-model-configuration-guide)).

![](docs/assets/S42-界面语言切换（同一页面的中英对照）.png)

## Role names follow the language; member names don't translate

When the interface language switches, localization follows one clear rule:

- **Role names translate with the language** — role names like Zinnia, Coordinator, and Worker are rendered in the interface language;
- **Member names are never translated** — concrete member names such as 林知远 and 苏禾 are identity markers and stay exactly as-is in any language.

Why it's designed this way: roles are universal concepts inside the product — localizing them lets users of every language map them correctly; member names are markers of a specific identity — translating them would break the consistency of "who this person is".

## Common questions

**Q: After switching the interface language, will chat content and member replies be translated?**
No. Switching only affects UI text (menus, buttons, role names, and more); chat content and member output stay as-is.

**Q: Will member names become English in the English interface?**
No. Member names are never translated and stay as-is in any language; only role names follow the language.

**Q: Why does the theme follow the system?**
Knowe defaults to following the system appearance so the app's look stays consistent with the system; documentation screenshots are all in the light theme.

**Q: Does large text mode affect members' work?**
No. It only affects how the interface displays — it doesn't affect the team's approvals, execution, or output.

## Next steps

- Want to set a display name, an avatar, and check the version? → [30 Configuration · Account and Identity](30-04-Account-and-Identity.md)
- Want to adjust notification and tray behavior? → [30 Configuration · Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md)
- Want to revisit the language choice at first launch? → [02 Installation and System Requirements · First launch: model configuration guide](02-Installation-and-System-Requirements.md#first-launch-model-configuration-guide)

---

**Previous**: [30-02 Approvals, Notifications, and the Tray](30-02-Approvals-Notifications-and-Tray.md)
**Next**: [30-04 Account and Identity](30-04-Account-and-Identity.md)
