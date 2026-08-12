<!--
  Page: 60 Troubleshooting · Model and Runtime Issues
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 60 Troubleshooting · Model and Runtime Issues
  Status: published (eleventh batch)
-->

# 60 Troubleshooting · Model and Runtime Issues

> **At a glance**: the model is the brain the team works with; this page focuses on "model-related runtime issues": how to troubleshoot a failed connection test (the three causes: Key / network / provider), what to do when a member reports "the model doesn't support this format", how to **Stop** a member who is stuck, and how the fallback model automatically covers you. Each type is organized as "symptom → troubleshoot → resolve", telling you **where to look and what to click**.

**On this page**

- [Symptom quick reference: three common problems](#symptom-quick-reference-three-common-problems)
- [Symptom 1: the connection test fails](#symptom-1-the-connection-test-fails)
- [Symptom 2: a member reports "the model doesn't support this format"](#symptom-2-a-member-reports-the-model-doesnt-support-this-format)
- [Symptom 3: a member is stuck](#symptom-3-a-member-is-stuck)
- [The fallback model: the automatic safety net](#the-fallback-model-the-automatic-safety-net)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Symptom quick reference: three common problems

| Symptom | Go to |
|:--|:--|
| The connection test fails after changing the provider / model / Key | [Symptom 1: the connection test fails](#symptom-1-the-connection-test-fails) |
| A member reports "the model doesn't support this format" while working | [Symptom 2](#symptom-2-a-member-reports-the-model-doesnt-support-this-format) |
| A member stays "Working" and the streaming output stops advancing | [Symptom 3: a member is stuck](#symptom-3-a-member-is-stuck) |
| The primary model is temporarily unavailable (rate limited / flaky / expired Key) and the team is stuck | [The fallback model: the automatic safety net](#the-fallback-model-the-automatic-safety-net) |

Connection (badge / backend) problems themselves aren't on this page — go back to [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md) first.

## Symptom 1: the connection test fails

**Symptom**: after changing the three ingredients of the primary model in Settings → Models and Providers, the connection test fails; or the step-2 test in the first-run setup fails — the card gives the specific reason (see [30 Configuration · Models and Providers · Primary model: binding and the connection test](30-01-Models-and-Providers.md#primary-model-binding-and-the-connection-test)).

**Troubleshoot**: the failure has three common causes:

| Reason | Meaning | Troubleshoot / resolve |
|:--|:--|:--|
| **Authentication failure** | The API Key is wrong or expired | Check that the Key is complete with no leading or trailing spaces; change it and test again |
| **Network unreachable** | Can't reach the vendor's API | Check your network and proxy allowance — see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements) |
| **Insufficient balance / rate limited** | Limits on the vendor side | Check the quota and rate-limit status in the vendor console |

**Resolve**:

1. After fixing it per the table above, click "Test and Apply" again (or the re-test button on the setup card);
2. The first-run setup has a three-step quick self-check covering most cases: ① whether the API Key is complete with no leading or trailing spaces; ② whether this machine can reach the vendor's website / API domain; ③ whether a corporate proxy is blocking it (see [02 Installation and System Requirements · Network requirements](02-Installation-and-System-Requirements.md#network-requirements));
3. And don't forget: the API Key is kept on this machine only, never written to browser storage, and can be cleared and re-entered in Settings (see [API Key security: not written to disk](30-01-Models-and-Providers.md#api-key-security-not-written-to-disk)).

## Symptom 2: a member reports "the model doesn't support this format"

**Symptom**: while working, a member reports an error like "the model doesn't support this format", and an attachment can't be read in.

**Troubleshoot**:

1. First confirm whether the file is in the list on [20 Guides · Files and Attachments · Which formats are supported](20-05-Files-and-Attachments.md#which-formats-are-supported);
2. The format list is just a statement of the "supported range" — whether the AI can actually read a specific file also depends on the file itself (corrupted, encrypted, and so on — see [Files and Attachments · Common questions](20-05-Files-and-Attachments.md#common-questions)).

**Resolve**: if it's not in the supported list, **convert the content into a supported format** before feeding it — for example, export it as PDF, text, or a spreadsheet, or **copy the key content straight into a message** (the same line as [50 Reference · FAQ · Files](50-04-FAQ.md#files)).

> **Tip**: what the model reads is **the file's content** (images go through multimodal direct reading, the rest are packed into file content blocks), not "the path" — "can't read" is usually a format or file problem, not a path problem (see [How the AI reads attachments](20-05-Files-and-Attachments.md#how-the-ai-reads-attachments)).

## Symptom 3: a member is stuck

**Symptom**: the member's status stays "Working" and the streaming output stops advancing (the word-by-word output has stopped).

**Troubleshoot**: first check whether their streaming output is still advancing; only act once it's really stuck (the same line as [20 Guides · Assign and Accept · Common questions](20-03-Assign-and-Accept.md#common-questions)).

**Resolve**: **Stop** them in the **roster**:

1. In the roster, find the member whose status is **Working** and click the "**Stop**" button on their row;
2. Clicking it doesn't execute immediately — an **inline double confirmation** appears (like "Confirm stopping 顾言's current task?"); **if you don't confirm within 5 seconds, the operation auto-cancels**;
3. After you confirm, the member interrupts the current work and the status returns to "On standby"; **the intermediate output already produced and the records in the chat stream are all kept**;
4. If you want to continue after stopping, **assign the task again** — what was stopped is "this one execution", not the member.

Operation details and screenshots are in [20 Guides · Manage the Team · Stop](20-02-Manage-Team.md#stop-interrupt-a-working-member).

> **Tip**: if more than one member isn't moving, first go back to [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md) and check whether the badge says "Connected" — members can also appear "stuck" when the connection is abnormal (the same line as [Manage the Team · Common questions](20-02-Manage-Team.md#common-questions)).

## The fallback model: the automatic safety net

When the primary model is temporarily unavailable (vendor-side rate limiting, network hiccups, an expired Key), a configured **fallback model** automatically steps in, so the team keeps working instead of stalling on a primary-model failure (see [30 Configuration · The fallback model and automatic fallback](30-01-Models-and-Providers.md#the-fallback-model-and-automatic-fallback)).

Key points:

- The fallback model is configured in **Settings → Models and Providers**, side by side with the primary model, and needs the same things: a provider, a model, and a usable API Key;
- Day-to-day work still runs on the primary model; the fallback only steps in when the primary is unavailable;
- For reliability-sensitive setups, consider a fallback model from a **different provider** than the primary one — spreading out the impact of a single-provider failure.

If no fallback model is configured, troubleshoot and fix it per [Symptom 1](#symptom-1-the-connection-test-fails) when the primary model is unavailable.

## Common questions

**Q: The connection test passed, but members still report errors while working?**

See which category the error falls into: "the model doesn't support this format" → [Symptom 2](#symptom-2-a-member-reports-the-model-doesnt-support-this-format); stuck and not moving → [Symptom 3](#symptom-3-a-member-is-stuck); persistent errors → first go back to [Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md) and check the badge.

**Q: I changed the Key but still get authentication failure?**

Check that the Key is complete with no leading or trailing spaces, and confirm you copied the Key of the selected provider; if needed, clear it in Settings and re-enter it before testing again (see [Symptom 1](#symptom-1-the-connection-test-fails) and [API Key security: not written to disk](30-01-Models-and-Providers.md#api-key-security-not-written-to-disk)).

**Q: Can the fallback model be unavailable too?**

Yes. That's why it's recommended to pick a **different provider** from the primary model, spreading out the impact of a single-provider failure (see [The fallback model and automatic fallback](30-01-Models-and-Providers.md#the-fallback-model-and-automatic-fallback)).

**Q: Does stopping a member lose progress?**

It doesn't lose what was already produced: files a member wrote are in the project directory, and the chat records are in the project memory; what's lost is only "this unfinished run" — assign it once more and you're back on track (see [Manage the Team · Common questions](20-02-Manage-Team.md#common-questions)).

## Next steps

- The model is back to normal — continue the main loop → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- Want to switch models / bind models per member? → [30 Configuration · Models and Providers](30-01-Models-and-Providers.md)
- A file can't be read and you want to convert the format? → [20 Guides · Files and Attachments](20-05-Files-and-Attachments.md)
- The connection badge is abnormal → [60 Troubleshooting · Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md)
- Directory or data problems → [60 Troubleshooting · Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)

---

**Previous**: [60-01 Connection and Backend Issues](60-01-Connection-and-Backend-Issues.md)
**Next**: [60-03 Directory and Data Recovery](60-03-Directory-and-Data-Recovery.md)
