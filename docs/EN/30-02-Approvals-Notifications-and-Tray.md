<!--
  Page: 30 Configuration · Approvals, Notifications, and the Tray
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 30 Configuration · Approvals, Notifications, and the Tray
  Status: published (seventh batch)
-->

# 30 Configuration · Approvals, Notifications, and the Tray

> **At a glance**: this page covers three groups of settings about being interrupted: the **approval timeout** (how long an Approval Card waits for you: 5 / 10 / 30 / 60 / 180 / 300 seconds, or no limit); the **desktop notifications** toggle (whether the system pops up reminders); and **close to tray** (closing the window ≠ quitting — the app stays in the system tray and keeps working) together with **tray cards for new messages** (you know a message arrived without opening the window).

**On this page**

- [Approval timeout](#approval-timeout)
- [The desktop notifications toggle](#the-desktop-notifications-toggle)
- [Close to tray](#close-to-tray)
- [Tray cards for new messages](#tray-cards-for-new-messages)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Approval timeout

Every Approval Card carries a countdown; when it runs out, the card is automatically withdrawn (final state **Timed out**; see [10 Core Concepts · Approval Mechanism · Countdown and the four final states](10-04-Approval-Mechanism.md#countdown-and-the-four-final-states)). The countdown has a sensible default length, and you can change it to your own rhythm in **Settings**:

- Available lengths: **5 / 10 / 30 / 60 / 180 / 300 seconds, or "no limit"**;
- With "no limit", cards never time out automatically — they stay up waiting for your call (you can still reject or cancel manually);
- Changes take effect **immediately**, for cards that pop up afterwards — cards already shown keep the setting from when they appeared;
- The countdown runs on the **server-side clock** — whether the interface sits on another window or is minimized to the background, the timer keeps running and is never reset by switching windows or refreshing.

> The approval mechanism itself — why approvals exist, what the four kinds of cards look like, and the four final states — is on [10 Core Concepts · Approval Mechanism](10-04-Approval-Mechanism.md); this page only covers how to set the timeout.

## The desktop notifications toggle

Knowe can pop **desktop notifications** at the system level to remind you of things worth pausing for — a new Approval Card appearing, a new message arriving, and the like. The toggle is in **Settings → Notifications**:

- **On** — when something needs you, the system pops a desktop notification;
- **Off** — no more system notifications — the Approval Card still stays in the chat stream waiting for you (see [10 Core Concepts · Approval Mechanism](10-04-Approval-Mechanism.md)); it just stops interrupting you.

## Close to tray

- **Clicking the window's close button minimizes Knowe to the system tray** (the icon area at the right end of the taskbar) instead of quitting;
- The app keeps running in the **background**: members work as usual, Approval Cards keep counting down (the countdown runs on the server-side clock — see [10 Core Concepts · Approval Mechanism · Countdown and the four final states](10-04-Approval-Mechanism.md#countdown-and-the-four-final-states)), and new messages keep arriving;
- To get back to the interface, click the tray icon to restore the window; to fully quit, exit the app from the tray icon.

> Read together with [The desktop notifications toggle](#the-desktop-notifications-toggle): "not interrupting you" and "not quitting" are two different things — the former turns off system notifications, the latter only tucks the window into the tray.

## Tray cards for new messages

After closing to tray, Knowe isn't a "silent background" — when new messages arrive (a new Approval Card, a new group-chat message, and so on), **the tray pops up a notification card**, so you know something happened without opening the window, and you can click anytime to look.

![](docs/assets/S40-通知与托盘（两张并排）.png)

## Common questions

**Q: What happens if I set the approval timeout to "no limit"?**
Cards no longer time out automatically and stay up waiting for your call; you can still reject or cancel manually (see [10 Core Concepts · Approval Mechanism · Changing the approval timeout](10-04-Approval-Mechanism.md#changing-the-approval-timeout)).

**Q: Does the team keep working after I close to tray?**
Yes. The app keeps running in the background — members work as usual, approval countdowns keep ticking (server-side clock), new messages keep arriving, and tray cards remind you.

**Q: Will turning off desktop notifications make me miss approvals?**
No. Only the system pop-ups are turned off; the Approval Card stays in the chat stream and is there when you come back to the app.

**Q: How do I fully quit Knowe?**
Quit from the Knowe icon in the system tray. Clearing data or uninstalling the app is a different matter — see [30 Configuration · Account and Identity · About](30-04-Account-and-Identity.md).

## Next steps

- Want to tune how the interface looks and which language it speaks? → [30 Configuration · Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md)
- Want to review the approval mechanism and the four kinds of cards? → [10 Core Concepts · Approval Mechanism](10-04-Approval-Mechanism.md)
- Want to change models, configure a fallback model, or bind per member? → [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)

---

**Previous**: [30-01 Models and Providers](30-01-Models-and-Providers.md)
**Next**: [30-03 Appearance and Interface Language](30-03-Appearance-and-Interface-Language.md)
