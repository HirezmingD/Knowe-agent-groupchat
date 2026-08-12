<!--
  Page: 50 Reference · Roles Catalog
  Document: Knowe Desktop App · Technical Documentation
  Planning reference: docs/文档结构规划.md §4 · 50 Reference · Roles Catalog
  Status: published (tenth batch)
-->

# 50 Reference · Roles Catalog

> **At a glance**: this page is a quick-reference catalog of all **24 worker roles** in Knowe: one line of "Good at" and one line of "Not suitable for" per role, plus decision guidance for **how to pick a role**. The role names match the in-product Roles Catalog; internal identifiers like "prefix / id" ship with each version and follow the product — this page doesn't invent any. The whole mindset of picking people comes down to one line: **same tools, different minds.**

**On this page**

- [The 24 roles table](#the-24-roles-table)
- [How to pick a role: decision guidance](#how-to-pick-a-role-decision-guidance)
- [Common misunderstanding: Good at / Not suitable for isn't a verdict on ability](#common-misunderstanding-good-at--not-suitable-for-isnt-a-verdict-on-ability)
- [Relationship to the in-product Roles Catalog](#relationship-to-the-in-product-roles-catalog)
- [Next steps](#next-steps)

---

## The 24 roles table

Workers come in **24 roles**, each matching one kind of professional judgment. In the table below, "Good at" means handing this kind of judgment to this role is the least hassle; "Not suitable for" means handing it over will most likely go off track — both are **division-of-labor suggestions**, not an ability ranking (how to read them: [Common misunderstanding](#common-misunderstanding-good-at--not-suitable-for-isnt-a-verdict-on-ability)).

| Role | Prefix / id¹ | Good at | Not suitable for |
|:--|:--|:--|:--|
| **Frontend** | — | Browser behavior, state management, styling details, and page interactions | Server-side architecture and data-layer judgments |
| **Backend** | — | Server-side logic, API design, data flow, and system integration | Visual, interaction, and copy-tone judgments |
| **Product** | — | Defining the problem, prioritizing requirements, weighing user value | Concrete implementation details (code / styling) judgments |
| **QA** | — | Designing test cases, finding edge cases, questioning "what could go wrong" | Brand tone and creative direction judgments |
| **UI/UX design** | — | User journeys, visual hierarchy, consistency | Pure logic and algorithm implementation judgments |
| **Data analysis** | — | Organizing data, statistics and classification, finding patterns, making charts | Subjective creativity and copy expression judgments |
| **DevOps** | — | Deployment, environments, dependencies, and change management | Product positioning and user research judgments |
| **Security** | — | Threat modeling, attack surface, risk and compliance boundaries | Interface visuals and interaction judgments |
| **AI / Machine Learning** | — | Model selection, data preparation, training and evaluation | Business copy and operations judgments |
| **Mobile** | — | Mobile platform behavior, adaptation, performance, and release | Server-side architecture and data-layer judgments |
| **Game** | — | Gameplay, balance, levels, and game loops | Serious documentation and compliance judgments |
| **GIS** | — | Spatial data, coordinate systems, maps, and spatial analysis | Brand marketing and copy creativity judgments |
| **Marketing** | — | Audience, conversion goals, and communication strategy | Code implementation and system architecture judgments |
| **Finance / Accounting** | — | Cost, accounting terms, financial logic, and compliance risk | Creative design and interaction experience judgments |
| **Healthcare** | — | Healthcare terminology, workflows, and compliance sensitivity | Internet product growth and operations judgments |
| **Academic / Education** | — | Knowledge organization, teaching structure, citation norms | Engineering implementation and performance optimization judgments |
| **Spatial computing** | — | 3D spatial interaction, spatial design, and human-computer boundaries | Traditional documentation and compliance judgments |
| **Technical support** | — | Diagnosing user issues, phrasing, and resolution paths | Product strategy and architecture decision judgments |
| **Site reliability** | — | Failure surface, dependency chains, recovery order, and reliability | Content creation and brand expression judgments |
| **Database** | — | Data modeling, query optimization, migration, and consistency | Interface interaction and visual judgments |
| **Architecture** | — | System decomposition, technology selection, trade-offs | Fine-grained execution (specific page / copy) judgments |
| **Technical writing** | — | Structuring documentation, organizing information clearly, writing it up | Operations growth and campaign strategy judgments |
| **Audio/Video** | — | Audio/video processing, formats, editing, and media acceptance | Finance and compliance judgments |
| **Legal / Compliance** | — | Wording risk and compliance boundaries | Frontend interaction and product creativity judgments |

¹ "Prefix / id" is the product-internal identifier for a worker role; in the interface, the member's name and role label are what you see. The exact values ship with each version and follow the product — this page doesn't invent any.

> **Tip**: the "Good at / Not suitable for" columns above are a documentation-scope quick reference; **when you actually assign work, go by the candidates the Coordinator proposes and the member's profile page** — that's the same role table rendered live in the product.

## How to pick a role: decision guidance

Follow these points and you can pick the right person without memorizing the table:

1. **Let the Coordinator propose first; you just confirm** — the Build Team card and the Task card both show the candidates' "Good at" notes, so hand the pick to the Coordinator and you keep the final say (see [20 Guides · Create a Project and Build a Team · Step 4](20-01-Create-Project-and-Build-Team.md#step-4--confirm-the-build-team-card-build-the-team));
2. **Pick by "who should think about this"** — first ask what kind of judgment the core of the task needs: users and experience → UI/UX design, Product; logic and implementation → Frontend, Backend, Architecture; data and facts → Data analysis, Database; risk and compliance → Security, Legal; expression and organization → Technical writing, Marketing (details: [10 Core Concepts · Why assignment picks by role](10-02-Zinnia-Coordinator-and-Workers.md#why-assignment-picks-by-role));
3. **Respect the "Not suitable for" boundary** — asking Legal to write frontend interactions or QA to set the brand tone isn't "they can't do it"; it's that the direction of judgment will most likely be wrong, and the rework costs more;
4. **You don't need a full team on the first round** — roles can be added anytime (let the Coordinator propose and you confirm one Build Team card — see [20 Guides · Manage the Team · Adding members](20-02-Manage-Team.md#adding-members-let-the-coordinator-propose));
5. **When you want to pick people yourself** — the roster and the member profile page (areas of expertise, current status, permission boundaries) are your basis for deciding (see [20 Guides · Search, Favorites, and Contacts · The contact profile page](20-07-Search-Favorites-and-Contacts.md#the-contact-profile-page)).

A mapping that puts "judgment type → role" into practice (excerpted from published pages):

| Scenario | Hand it to | Why |
|:--|:--|:--|
| Building a page with interactions | A Frontend Worker | The judgment lives in browser behavior, state management, and styling details |
| Setting the information architecture and visual guidelines | A UI/UX design Worker | The judgment lives in user paths, hierarchy, and consistency |
| Troubleshooting an unavailable production service | A Site reliability / DevOps Worker | The judgment lives in the failure surface, the dependency chain, and the recovery order |
| Drafting a compliance statement | A Legal Worker | The judgment lives in wording risk and compliance boundaries |
| Producing a product research report | A Product / Data analysis Worker | The judgment lives in problem definition and evidence interpretation |

## Common misunderstanding: Good at / Not suitable for isn't a verdict on ability

- **Not an ability ranking** — "Good at" isn't "only they can do it", and "Not suitable for" isn't "they can't do it"; both describe the **direction of judgment**: who's the least hassle to hand this to, and who will most likely go off track (see [10 Core Concepts · Same tools, different minds](10-02-Zinnia-Coordinator-and-Workers.md#same-tools-different-minds));
- **Same tools, different minds** — every member has the exact same toolbox (reading and writing files, running commands, checking facts online, calling models); the only difference is professional judgment — so picking people is picking judgment, not toolboxes;
- **A role label isn't a fixed division of labor** — what the same role does in different projects is decided by the tasks; who actually gets a task follows the Coordinator's proposal and your confirmation.

## Relationship to the in-product Roles Catalog

- The role names above match the **in-product Roles Catalog** (Frontend, Backend, Product, QA, UI/UX design, Data analysis, DevOps, Security, AI / Machine Learning, Mobile, Game, GIS, Marketing, Finance / Accounting, Healthcare, Academic / Education, Spatial computing, Technical support, Site reliability, Database, Architecture, Technical writing, Audio/Video, Legal / Compliance — per the product);
- The "prefix / id" in the table, the exact wording of role labels, and the "Good at / Not suitable for" copy in the interface ship with each version and follow the product — this page only gives a documentation-scope quick reference and doesn't invent internal identifiers;
- Member names (like 江澈, 顾言) are generated per project and live on a different axis from roles: **names don't translate, and avatars are bound for life** (see [10 Core Concepts · Workers](10-02-Zinnia-Coordinator-and-Workers.md#workers-24-roles-addable-and-removable)), while role names translate with the interface language (see [30 Configuration · Appearance and Interface Language · Role names follow the language; member names don't translate](30-03-Appearance-and-Interface-Language.md#role-names-follow-the-language-member-names-dont-translate)).

## Next steps

- Want to understand the division of the three roles (Zinnia / the Coordinator / Workers)? → [10 Core Concepts · Zinnia, the Coordinator, and Workers](10-02-Zinnia-Coordinator-and-Workers.md)
- Want to see how the role table is used when assigning work? → [20 Guides · Assign and Accept](20-03-Assign-and-Accept.md)
- Want to manage the roster (add, remove, invite back, stop)? → [20 Guides · Manage the Team](20-02-Manage-Team.md)
- Continue the reading path → [50 Reference · Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md)

---

**Previous**: [40-04 Environment Variables and Deployment Modes](40-04-Environment-Variables-and-Deployment.md)
**Next**: [50-02 Markdown and Formula Rendering](50-02-Markdown-and-Formula-Rendering.md)
