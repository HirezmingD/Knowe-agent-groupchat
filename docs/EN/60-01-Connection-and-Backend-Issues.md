<!--
  Page: 60 Troubleshooting · Connection and Backend Issues
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 60 Troubleshooting · Connection and Backend Issues
  Status: published (eleventh batch)
-->

# 60 Troubleshooting · Connection and Backend Issues

> **At a glance**: Knowe is a **locally running** app: the **connection status badge** at the top of the window shows the connection between the app and the local backend — in normal operation it says "Connected". This page walks through three kinds of connection / backend issues in a "symptom → troubleshoot → resolve" flow: what to do when the badge is abnormal (Connecting / Reconnecting / Disconnected, and others), how the backend auto-restarts after a crash and how to restart it manually with "Retry", and what happens when a port is occupied. In most cases, clicking "Retry" next to the badge is all it takes.

**On this page**

- [The connection status badge: read it first](#the-connection-status-badge-read-it-first)
- [Symptom 1: the badge is disconnected or keeps reconnecting](#symptom-1-the-badge-is-disconnected-or-keeps-reconnecting)
- [Symptom 2: the backend crashed (or never started)](#symptom-2-the-backend-crashed-or-never-started)
- [Symptom 3: a port is occupied](#symptom-3-a-port-is-occupied)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## The connection status badge: read it first

The badge sits at the **top of the window (top-right corner)** and is the real-time indicator of the connection between the app and the local backend, with states like **Connecting / Connected / Reconnecting / Disconnected** (the exact state set follows the product — see [40 Advanced · Ports and the backend: the local running mode](40-04-Environment-Variables-and-Deployment.md#ports-and-the-backend-the-local-running-mode); term definitions are in [50 Reference · Glossary](50-03-Glossary.md)).

- **"Connected" is the sign of normal operation** — glancing at the badge before sending messages or assigning work is the fastest self-check;
- When the badge isn't "Connected", requests posted in the group chat may go unanswered for a long time — handle the badge first, then everything else (the same line as the tip in [20 Guides · Create a Project and Build a Team · Before you begin](20-01-Create-Project-and-Build-Team.md#before-you-begin)).

## Symptom 1: the badge is disconnected or keeps reconnecting

**Symptom**: the badge shows states like "Reconnecting / Disconnected" and doesn't get back to "Connected" for a long time; requests posted in the group chat aren't picked up.

**Troubleshoot** (in order):

1. First click "**Retry**" next to the badge to restart the backend — this is step one, and it resolves most cases (the same order as [50 Reference · FAQ · Connection](50-04-FAQ.md#connection));
2. If that doesn't work, check whether your local network is fine: are you offline, or in a corporate proxy environment that requires allowing HTTPS outbound to the model provider (see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements));
3. Then check whether other programs are occupying the backend ports (see [Symptom 3](#symptom-3-a-port-is-occupied) below).

**Resolve**: after clicking "Retry", the badge usually returns to "Connected"; if it keeps dropping, keep troubleshooting along the two lines "network → ports".

## Symptom 2: the backend crashed (or never started)

**Symptom**: the badge shows "Disconnected" or stays on "Connecting"; operations don't respond.

**Troubleshoot**: Knowe bundles its own backend process (WS 8080 / HTTP 8081 by default — see [40 Advanced · Ports and the backend: the local running mode](40-04-Environment-Variables-and-Deployment.md#ports-and-the-backend-the-local-running-mode)). When the backend is abnormal, the app **auto-restarts (auto-relaunches)** it — wait a few seconds and see whether the badge recovers to "Connected" on its own.

**Resolve**: if the auto-restart doesn't take effect, click "**Retry**" next to the badge to restart the backend manually. Restarting just relaunches the local backend process and doesn't affect your local data — the data is all in `data/` and `Logs/` under the installation directory (see [40 Advanced · The data directory: data and Logs](40-04-Environment-Variables-and-Deployment.md#the-data-directory-data-and-logs)).

## Symptom 3: a port is occupied

**Symptom**: you suspect a port conflict is stopping the backend from starting or causing repeated disconnections.

**Troubleshoot**: the backend runs on this machine, using **WS 8080 / HTTP 8081** by default; when a port is occupied it **auto-avoids** to another port — generally no manual handling needed (see [40 Advanced · Ports and the backend: the local running mode](40-04-Environment-Variables-and-Deployment.md#ports-and-the-backend-the-local-running-mode)). When troubleshooting, check whether other programs on this machine (local dev servers, other apps, and so on) are using these two ports.

**Resolve**: generally nothing to handle — auto-avoidance is enough. If you confirm a program is occupying 8080 / 8081 long-term and it's affecting use, stop the occupying program, then click "Retry" to restart the backend.

## Common questions

**Q: Why can't I connect even after clicking "Retry"?**

Keep troubleshooting in the order of Symptom 1: first confirm the local network is fine (the model provider's API is reachable — see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements)), then check whether a program is occupying 8080 / 8081 (see [Symptom 3](#symptom-3-a-port-is-occupied)).

**Q: How long should I wait while the badge is stuck on "Connecting"?**

Normally it enters "Connected" quickly; if it stays on "Connecting / Reconnecting" for a long time, troubleshoot through Symptom 1 and Symptom 2 in order.

**Q: Does a disconnection lose data?**

No. All of Knowe's data is on this machine (`data/` and `Logs/` under the installation directory — see [40 Advanced · The data directory](40-04-Environment-Variables-and-Deployment.md#the-data-directory-data-and-logs)) and doesn't depend on any cloud service; a disconnection affects only the real-time connection to the local backend, not the data itself.

## Next steps

- Connection is back to normal — continue the main loop → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- The badge is fine but members don't move or models error → [60 Troubleshooting · Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md)
- Want to review the badge and port definitions? → [40 Advanced · Environment Variables and Deployment Modes · Ports and the backend: the local running mode](40-04-Environment-Variables-and-Deployment.md#ports-and-the-backend-the-local-running-mode)
- Connection questions at a glance → [50 Reference · FAQ · Connection](50-04-FAQ.md#connection)

---

**Previous**: [50-04 FAQ](50-04-FAQ.md)
**Next**: [60-02 Model and Runtime Issues](60-02-Model-and-Runtime-Issues.md)
