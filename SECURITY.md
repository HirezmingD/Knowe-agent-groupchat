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
- **Sandboxed agents**: agents can only operate inside their project directory — they cannot read or modify files outside the project root.
- **Local ports**: the backend listens on `127.0.0.1` (WebSocket 8080, health 8081) — loopback only, not exposed to the network.
- **API keys**: model provider keys are stored via environment variables / user configuration; they are never committed to the repository.

## Scope

This policy covers the Knowe application and its source code. Vulnerabilities in third-party dependencies should be reported to their respective projects.
