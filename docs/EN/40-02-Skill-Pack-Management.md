<!--
  Page: 40 Advanced · Skill Pack Management
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 40 Advanced · Skill Pack Management
  Status: published (eighth batch)
-->

# 40 Advanced · Skill Pack Management

> **At a glance**: [20 Guides · Knowledge Base and Skill Packs](20-06-Knowledge-Base-and-Skill-Packs.md) mentioned that skill packs come in three types (system-bundled / project-experience / third-party); this page goes through all three: each one's **origin, modifiability, lifecycle, and who maintains it**; how **project-experience skills** are exported from the project's core knowledge and curated; and the **installation, uninstallation, and independent lifecycle** of third-party skill packs. The basic distinction between the three types is in [20 Guides · Knowledge Base and Skill Packs · Skill packs: three types, each with its own place](20-06-Knowledge-Base-and-Skill-Packs.md#skill-packs-three-types-each-with-its-own-place).

**On this page**

- [Three skill pack types, three lines of difference](#three-skill-pack-types-three-lines-of-difference)
- [System-bundled skills: built in, immutable](#system-bundled-skills-built-in-immutable)
- [Project-experience skills: export and curation](#project-experience-skills-export-and-curation)
- [Third-party skill packs: install, uninstall, and lifecycle](#third-party-skill-packs-install-uninstall-and-lifecycle)
- [When do you need to manage skill packs](#when-do-you-need-to-manage-skill-packs)
- [Common questions](#common-questions)
- [Next steps](#next-steps)

---

## Three skill pack types, three lines of difference

A skill pack is a **capability set** mounted on a project. The three types differ all the way from "where they come from" to "who maintains them":

| Type | Where it comes from | Modifiable? | Lifecycle | Who maintains it |
|:--|:--|:--|:--|:--|
| **System-bundled skills** | Built into the app (carrying SKILL.md definitions) | Immutable | Stays with the app | The app |
| **Project-experience skills** | Exported from the project's core knowledge, settling with the project | Curatable | Travels with the project | You (after approving) |
| **Third-party skill packs** | Installed from outside | Installable / uninstallable | Independent lifecycle | You + the provider |

One sentence to remember the difference (consistent with [20 Guides · Knowledge Base and Skill Packs · Skill packs: three types, each with its own place](20-06-Knowledge-Base-and-Skill-Packs.md#skill-packs-three-types-each-with-its-own-place)):

> **System skills come with the product, project-experience skills are what you build up yourselves, and third-party skill packs are installed from outside.**

## System-bundled skills: built in, immutable

- **Built into the app** — basic capabilities shipped with the app, defined in the bundled SKILL.md;
- **Immutable** — you can't modify or delete them: they're part of the app's capabilities and are there as soon as you open it;
- **Not part of curation** — they have nothing to do with project distillation or knowledge curation, and they don't grow or shrink with projects.

System skills are the "foundation": they guarantee the team has a baseline set of capabilities from the first day in a project, with no configuration needed.

## Project-experience skills: export and curation

**Project-experience skills** are the only one of the three that "grows with the project":

- **Exported from the project's core knowledge** — the reusable experience in the project that has been approved and settled (preferences, practices, pitfalls… see [20 Guides · Knowledge Base and Skill Packs · Knowledge assets: five types and four labels](20-06-Knowledge-Base-and-Skill-Packs.md#knowledge-assets-five-types-and-four-labels)) is organized into skills unique to this project;
- **Settles with the project** — the longer and the more a project is used, the more it settles, and the more project-experience skills grow; they travel with the project and are the capability "you build up yourselves";
- **Curatable** — the management approach shares its origin with [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md): which settled knowledge is worth becoming a skill, and which skills have gone stale and can be retired. Curation targets knowledge; a skill is another organizational form of knowledge (memory = process, knowledge = distilled, see [10 Core Concepts · Memory and Context · Knowledge base and memory: division of labor](10-05-Memory-and-Context.md#knowledge-base-and-memory-division-of-labor)).

> **Tip**: project-experience skills aren't written by hand — they're "exported": the project's already-settled core knowledge is organized into skills the team can reuse. So they're directly tied to the quality of your knowledge-asset curation: the more tidily the knowledge base is built up, the more reliable project-experience skills are.

## Third-party skill packs: install, uninstall, and lifecycle

**Third-party skill packs** are the only one of the three that "comes from outside":

- **Installed from outside** — skills provided by third parties, independent of what's built into the app and of project distillation;
- **Independent install directory** — they're stored in their own location after installation, not mixed into project data or the knowledge base;
- **Independent lifecycle** — install → use → uninstall, without interfering with project memory or knowledge curation: uninstalling a skill pack doesn't affect the knowledge and memory already in the project, and however you curate project knowledge, it doesn't affect installed third-party skill packs;
- **Uninstall** — remove a skill pack you no longer need (the uninstall entry and the confirmation method follow the interface).

> **Suggestion**: third-party skill packs come from outside, so when choosing one, mind its origin and update status — it doesn't go through the "Pending review / approve" flow of knowledge curation; judging whether it's reliable is up to your own choice.

## When do you need to manage skill packs

- **Most users: nothing to manage** — system skills work out of the box; project-experience skills grow on their own with the project; without external needs, third-party skill packs never come into the picture;
- **When you do need to manage**:
  - The project has settled to a certain scale and you want to sort out its project-experience skills (which are worth keeping, which should be retired) → follow the approach of [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md);
  - You want to bring in external capabilities (install a third-party skill pack);
  - You want to remove a third-party skill pack you no longer need (uninstall).

## Common questions

**Q: Can system skills be modified?**
No. System-bundled skills are basic capabilities built into the app (defined in SKILL.md) and are immutable — they're the only one of the three you never have to manage.

**Q: What's the relationship between project-experience skills and knowledge assets?**
Project-experience skills are **exported** from the project's core knowledge — knowledge is the distilled part, and a skill is one organizational form of it; the curation approach is the same as [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md).

**Q: Will uninstalling a third-party skill pack lose data?**
No. Third-party skill packs have an independent lifecycle; uninstalling only removes the skill itself, without affecting project memory, the knowledge base, or workspace files.

**Q: Do the three skill pack types affect each other?**
No. System-bundled / project-experience / third-party are independent of each other: different origins, different lifecycles, different maintenance.

## Next steps

- Want to understand DMs, memory, and permission boundaries? → [40 Advanced · DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md)
- Want to review knowledge-asset curation (approve / reject / retire / delete / evidence deep-dive)? → [40 Advanced · Knowledge Curation](40-01-Knowledge-Curation.md)
- Want to review the basic distinction between the three types? → [20 Guides · Knowledge Base and Skill Packs · Skill packs: three types, each with its own place](20-06-Knowledge-Base-and-Skill-Packs.md#skill-packs-three-types-each-with-its-own-place)

---

**Previous**: [40-01 Knowledge Curation](40-01-Knowledge-Curation.md)
**Next**: [40-03 DMs, Memory, and Permission Boundaries](40-03-DM-Memory-and-Permission-Boundaries.md)
