# Security Policy

**简体中文版：[SECURITY.zh-CN.md](./SECURITY.zh-CN.md)**

## Reporting a vulnerability

If you discover a security vulnerability in Knowe, please report it privately — do **not** open a public issue.

**How to report:**
- Email: **jhezhou@gmail.com** (or jhezhou@163.com)
- Include in your report: affected version, vulnerability description, reproduction steps, and (if possible) a proof of concept.

We aim to acknowledge reports within 3 business days and will keep you informed of the fix timeline.

## Security design notes

- **Local-first**: all data (projects, chat history, knowledge base, token ledger) stays on the user's machine. There is no cloud storage and no telemetry.
- **Sandboxed execution**: model-authored shell and Python processes run through Microsoft Execution Containers on supported Windows 11 hosts, with the project workspace as the only writable root and outbound network disabled. If native isolation cannot be proved, terminal execution fails closed. File tools remain a narrow, path-validated broker; only files explicitly approved by the user (such as signed attachments) may be read from outside the workspace.
- **Preview boundary**: the pinned MXC 0.7 runtime uses real Windows AppContainer and Job Object primitives, but Microsoft still labels MXC an early preview and explicitly says its current profiles must not yet be treated as a complete security boundary. Knowe adds fixed policy, link/ACL gates, a separate resource-limiting Job supervisor, and hostile-path tests; this is OS-enforced process containment, not a VM or a substitute for running truly hostile code on a disposable machine.
- **Local ports**: the backend listens on `127.0.0.1` (WebSocket 8080, health 8081) — loopback only, not exposed to the network.
- **API keys**: keys entered in the UI are encrypted at rest with current-user Windows DPAPI and an authenticated settings projection. They are not stored in browser storage, passed to agent terminal processes, or committed to the repository. DPAPI protects offline storage; malware already running as the same Windows user remains outside this guarantee.
- **Web isolation**: Chromium's native sandbox remains enabled. Agent-controlled web and browser traffic is restricted to public HTTP(S) destinations, with redirects and subresources revalidated; loopback, LAN, link-local, and reserved addresses are denied.
- **Developer tooling and app integrity**: packaged builds disable Chrome and Node remote debugging and flip Electron binary fuses for `RunAsNode`, `NODE_OPTIONS`, and the Node inspector. The app loads only from an integrity-validated ASAR. Because the trusted renderer entries currently use `BrowserWindow.loadFile()`, `GrantFileProtocolExtraPrivileges` remains enabled for their module and subresource loading; untrusted project HTML is rendered only as a scriptless, sandboxed `srcdoc`, and the legacy `/preview/tree` HTTP surface is not available. Development debugging remains explicit opt-in and loopback-only.
- **Update boundary**: the local hardened build does not contact or install third-party upstream auto-updates by default, preventing an un-gated release from replacing these controls. Re-enable updates only after moving to a controlled, signed release channel.

## Scope

This policy covers the Knowe application and its source code. Vulnerabilities in third-party dependencies should be reported to their respective projects.
