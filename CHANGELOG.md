# Changelog

**简体中文版：[CHANGELOG.zh-CN.md](./CHANGELOG.zh-CN.md)**

Complete evolution history of Knowe, from inception to the current release.

Version line: **v0.1 (prototype) → v0.2 (React rewrite) → v1.0 (productization & installer)**

---

## v1.0.x — Productization

### v1.0.38 (2026-08-20)

**Anthropic protocol compatibility (core)**
- ProviderClient adds an `anthropic_messages` transport: one interface auto-adapts to OpenAI / Anthropic protocols
- New `knowe_core/anthropic_codec.py` codec: `/v1/messages` endpoint, `x-api-key` auth, OpenAI↔Anthropic request/response/stream translation (thinking, tool_use, index-tracked input_json_delta)
- Transport threaded through the whole engine: PM / worker / 知知 / aux all use the transport layer — configure whichever provider, thinking left to the provider (connectivity is what matters)
- Fixed `model_adapter.from_legacy` losing transport plus an `or` short-circuit bug, preventing an anthropic primary model from silently reverting to the openai transport
- Added `tests/test_anthropic_codec.py`, `tests/test_provider_anthropic_integration.py` (22 + 5 passed), zero new regressions

**Member name & avatar (core)**
- New **project-scoped** identity store `identity_store.py`: renaming a member / changing an avatar in one group affects only that group — same-role members in other groups are no longer contaminated
- Dropped the old global table (keyed by agent_id) that caused cross-group name/avatar leaks; legacy data auto-cleaned
- Renamed / re-avatared members survive restart (4 restart breakpoints fixed: backend custom-name delivery, frontend schema avatar, registerMember consumption, history replay not overwriting the authoritative name)
- PM is notified with the freshest roster on rename — no longer answers from stale history

**「xx助手」assistant titles & role descriptions (core)**
- 24 positions get assistant titles + descriptions table (reviewed word-by-word by 用户): UI/UX→美工设计助手, Frontend→界面设计助手, GIS→GIS助手, etc.
- Unified backend `_display_role` outlet: worker self-intro, PM team roster, member roster, approval cards all use the new「xx助手」naming — old functional labels no longer coexist
- Frontend `assistantRoleLabel` mapping unified; roster panel (RosterPanel) uses the new table

**Common UI fixes**
- Group-build / add-member card roles show assistant titles; edit button enlarged and inline with the name
- Chat-avatar right-click menu gains the correct「Change name/avatar」entry; edit panel confirms on Enter, layered shading (all from var() tokens)
- Role descriptions de-duplicated and aligned verbatim with the PRD table; PM avatar in the group grid no longer degrades to a text glyph

**Settings → About copy**
- Update source note:「自动链接官网更新」→「自动链接Github Release仓库更新，链接可能不稳定」
- Website marked「网站备案中，尚未上线」; website link display and address unified to knowe-agent.online
- Author's note adds a long-term-maintenance statement, contact email and author WeChat (join the beta user group)

**Official name changed to「Knowe 知知智能体」**
- Window title, tray, installer product name, About page, first-run page and sidebar logo unified to「Knowe知知智能体」
- Internal identifiers (bundle id com.knowe.app, process names, HTTP headers, domain, code identifiers) unchanged

Version bumped to 1.0.38

### v1.0.37 (2026-08-18)

**Prompt & workflow optimization (core)**
- PM (coordinator) prompt slimmed by 74% (299→72 lines, zh/en in sync); typo fix「项目经历」→「项目经理」
- Worker prompt localized: new `prompts/zh/worker_prompt.md` + `prompts/en/worker_prompt.md`, fixing Chinese-mode workers replying in English
- 知知 (personal companion) humanized — companion role, no prohibition lists; incomplete English version fixed
- Non-greedy task assignment (positive guidance, no prohibition lists)
- Dead-file cleanup: deleted `identity_block.md` ×2, `souls/worker.txt`, `backend/worker_prompt.md`; engine no longer writes SOUL.md / IDENTITY.md

**@tag & markdown input box (core)**
- Composer input rewritten from textarea to TipTap rich text: @tag mentions (reused picker, caret-anchored popup) + 8 markdown quick-input rules + native right-click menu IPC
- New `tiptapMarkdown` serializer: editor JSON ↔ markdown both ways (8 node types + mention), 16 unit tests
- @tag insertion auto-appends a real space — caret no longer overlaps the capsule edge (browser draws caret on inline-block borders; CSS margin cannot fix it)
- Bubble right-click copy respects selection (copy selection if selected inside the bubble, otherwise full text)
- Native Electron edit menu (cut/copy/paste/selectAll) via preload bridge
- Added @tiptap/react, starter-kit, extension-mention, extension-placeholder (^3.30.1)

Version bumped to 1.0.37

### v1.0.36 (2026-08-16)

**Custom API (core)**
- Model binding now supports a "Custom" provider for OpenAI-compatible endpoints (local / relay / token-plan): pick "Custom" to freely enter a Base URL and model name
- Base URL validated to start with http:// or https:// before saving / testing
- Auxiliary model follows the main model for custom providers (no cheap-tier mapping → reuses the main model config), with a UI note showing what is currently effective

**Token pricing distinguishes custom providers**
- Usage from non-official providers (custom endpoints) no longer matches official catalog prices — shown as "no price" instead of mis-billing against an official model that happens to share the same name
- Token usage panel labels custom-provider model rows with a "custom" suffix
- Agent-level cost no longer nulls out entirely when some usage is unpriced; priced portion is still summed and shown, unpriced portion is flagged separately
- Fixed cheap-tier mapping drift (gemini / nvidia tiers aligned with the current catalog)

Version bumped to 1.0.36

### v1.0.35 (2026-08-14)

**Approval-card misplacement — root-cause fix (core)**
- Relay animation state machine consolidated into a single settle gate: every exit path atomically restores DOM styles, height table, and animation state — eliminating protect-lock leakage and render-phase side effects
- Card positioning no longer depends on the animation staying alive (yield check now uses the state machine); cards stay correctly positioned even when the animation is interrupted (switching groups / backgrounding)
- No more card misplaced to the top / large blank gap at the bottom under multi-group concurrent switching

**Stop-action streaming bubble residue fix**
- Draining in-flight stream broadcasts on stop so "reasoning done" is always the last event; late reasoning deltas no longer re-create the "AI reasoning..." bubble
- Cancellation now also cancels the orphaned LLM streaming task, so no more late deltas
- Frontend adds scope-level liveness check on late/out-of-order reasoning deltas — drops them for already-idle workers

**Startup unread-message fix**
- Read watermark persisted and restored across restart: unread messages survive restart, read ones stay read
- Frontend reports the read watermark on group switch / window focus / mark-as-read; backend advances and persists it
- Unread semantics unified to "message count" (front and back now share one ledger), eliminating the "event sequence vs message count" mismatch
- First launch after upgrade for existing users: history counts as read, no more flooding all history as unread

Version bumped to 1.0.35

### v1.0.34 (2026-08-14)

**Token-saving mechanism (core)**
- Tool-result stream compression + query-aware projection on by default (backend switches, invisible in UI)
- Four-group controlled test (2026-08-14, real model, 10-round script): A (default dual-on) vs control — input -19.8%, cache-miss -18.7%, cost -17.8%; context peak 66.68% → 51.24%
- Compression fired 14 times, saved 138,789 chars; compression is the main cost saver, projection adds stacking gain on top; projection alone shows no gain — the default config is already optimal
- Zero quality loss: all four groups hit every key value (ERROR 1997 / WARN / PANIC / MODE / data anomalies / config check), task completion equivalent

**Token panel & context card redesign**
- Four-stat area + AI memory usage card (usage % / auto-compress / history prune), plain-language copy
- Savings unit changed to "~X tokens" (front-end approximation, labeled "approx." to stay honest)
- Panel bottom radius handed to the resize handle (no double-radius seam)

**Billing & price table**
- DeepSeek peak/off-peak pricing support (effective 8/17: peak 9:00-12:00 & 14:00-18:00 Beijing; off-peak = half of peak), rate auto-selected by request time
- Grok 4.6 added to model list & price table (official <200K / ≥200K tiers)
- Spent amounts frozen at write time: price-table updates never recalculate history (new tokens use new rates)

**Fixes**
- Fold detection fix: duplicate lines fold correctly after stripping line-number prefixes (`… N lines elided (knowe) …`), non-duplicate tails preserved
- English-mode fixes: units (times/entries/approx/tokens) and large-number abbreviations (K/M/B/T) follow the UI language

Version bumped to 1.0.34

### v1.0.33 (2026-08-12)
- Pre-open-source release: About page links point to official website & public repo
- Update source switched to GitHub Releases (provider: github, explicit owner/repo)
- Installer artifact renamed to hyphenated form (Knowe-Setup-x.y.z.exe) for GitHub download compatibility
- Version bumped to 1.0.33

### v1.0.32 (2026-08-10)
- Local storage optimization fix: migration path & maintenance loop crash fixed
- Task snapshot zlib compression (R1), daily rotation gz (R2)
- Snapshot trimming N=50 (R3), upgrade migration (R4), outbox delete-on-delivery (R5)

### v1.0.31 (2026-08-10)
- Local storage optimization: snapshot compression, daily rotation, trimming, migration, outbox cleanup
- Measured data footprint down 57%

### v1.0.30 (2026-08-10)
- Update shell pages de-branded from "install" wording
- Bilingual installer UI (26 language strings)
- Unified versioning 1.0.30 (single source)

### v1.0.29 (2026-08-10)
- Fixed: removed dead blockmap index logic
- Update shell shown during upgrades

### v1.0.28 (2026-08-10)
- Differential updates (blockmap): incremental downloads
- Cache moved to install dir; installer keeps sample; index backfilled
- About page shows major version

### v1.0.26.2 (2026-08-10)
- Three fixes: update-check prompt, external links via system browser, image preview token injection
- Dual-track versioning (later unified)

### v1.0.25.x (2026-08-07 ~ 08-09)
- Windows installer (electron-builder + NSIS + PyInstaller backend)
- Auto-update (electron-updater, self-hosted update server)
- Installer fixes: tray avatar, approval card z-order, website links, favorites copy
- About page: real website/GitHub links, author email
- Fix: worker terminal `mkdir -p` creating a literal `-p` directory on Windows (normalized command source)

### v1.0.24.x (2026-08-06 ~ 08-08)
- Backend structure normalization (backend/backend/* → backend/)
- Backend state ledger (task/decision/delivery audit chain)
- Multi-group concurrent card misalignment fixed (relay animation, three defenses)
- GPU animations + CPU multi-core; batched event consumption (16ms rAF window)
- Hidden-session stall guard; HTTP request injection fix; packaged-build auth fix

### v1.0.23.x (2026-08-03 ~ 08-06)
- Message forwarding across groups
- Actively pull Workers into a group; add agent mid-chat
- Reasoning module rework & auxiliary model selection
- Markdown rendering rework (unified pipeline + HTML cache: group switch 2.2s → 165ms)
- Message list virtualization
- Persistent session views; local state skeleton persistence
- English mode

### v1.0.22.x (2026-08-03)
- Zinnia loop fix; minor frontend fixes

### v1.0.21.x (2026-08-02 ~ 08-03)
- English mode (bilingual UI)
- Project directory relocation fix; frontend bug fixes

### v1.0.20.x (2026-08-01)
- Token usage stats rework; message hover card
- Token bug fixes

### v1.0.19.x (2026-07-31 ~ 08-01)
- Tray popup card; dark mode; search
- Multimodal input; provider list update; multiple fix iterations

### v1.0.18.x (2026-07-30 ~ 07-31)
- Agent capability audit; runtime UX audit
- WebSocket & delete bug fixes

### v1.0.17.x (2026-07-29)
- Agent lifecycle hotfix

### v1.0.16.x (2026-07-29)
- Worker v2.2 runtime audit & implementation

### v1.0.15.x (2026-07-28 ~ 07-29)
- Worker extreme simplification (v2.2)

### v1.0.14.x (2026-07-28)
- Worker tools implementation (terminal/file etc.)
- Worker rollout fix

### v1.0.13.x (2026-07-28)
- First-install five-issue fixes
- Zinnia entry implemented

### v1.0.12 ~ v1.0.1 (2026-07-26 ~ 07-28)
- Three-phase fix deliveries; Provider compatibility audit & fixes
- Harness full-chain audit; Fake runtime forensic audit
- Worker Provider audit & convergence
- Frameless window (v2)

### v1.0.0.1 (2026-07-26)
- Old-world / new-world & third-world design docs
- Initial commit: Electron shell + assets

---

## v0.2.x — React Rewrite

### v0.50 (2026-07-22)
- Worker loop redesign (phases C/D/E/F)
- Comprehensive bug fixes

### v0.49 (2026-07-22)
- Worker loop redesign (complete redesign)

### v0.48 (2026-07-22)
- Token usage monitoring & fixes

### v0.47 (2026-07-22)
- Worker token optimization

### v0.46 (2026-07-22)
- Zinnia HTML rendering fix

### v0.45.x (2026-07-21)
- Contacts & delete tools
- Protocol leak fix; five fixes; record delete fix; delete UX

### v0.44.x (2026-07-18 ~ 07-20)
- Settings panel completed
- Provider fixes; model-switch bug fix
- Long-term memory refactor; memory pre-retrieval; agent memory
- Conversation menu; agent menu
- Harness context fix; DM isolation
- DM bug fixes

### v0.43 (2026-07-18)
- Skill packs completed

### v0.42 (2026-07-18 ~ 07-19)
- Knowledge base refactor

### v0.41 (2026-07-18 ~ 07-19)
- Knowledge base delivery

### v0.40.x / v0.39.x / v0.38.x (2026-07-17 ~ 07-18)
- Multiple fix iterations

### v0.37.x (2026-07-17 ~ 07-18)
- Direct messages (DM) implemented; DM status; multiple fixes

### v0.36.x (2026-07-17)
- File preview delivery

### v0.35 (2026-07-17)
- Context handling

### v0.34.x / v0.33 / v0.32.x / v0.31 (2026-07-16 ~ 07-17)
- Iteration updates & fixes

### v0.30 (2026-07-16 ~ 07-17)
- Refactor iteration

### v0.29 / v0.28 / v0.27 / v0.26 / v0.25 / v0.24 / v0.23.x (2026-07-15 ~ 07-16)
- Iterations: harness weld, weld, morph animation, dialogue UX, natural dialogue, stream hotfix

### v0.22 (2026-07-15 ~ 07-16)
- Harness reliability

### v0.21 (2026-07-15 ~ 07-16)
- Dialogue fixes

### v0.20 (2026-07-13 ~ 07-15)
- Batch 4 implementation; knowledge refactor start

### v0.19 (2026-07-13 ~ 07-15)
- LLM Wiki implementation

### Unversioned milestones (interleaved in v0.2, 2026-07-16 ~ 07-24)
- Harness rework (multiple waves: WAVE 0/12/34/567/89)
- Infrastructure redesign deliverables
- Removed compiler/verifier
- Blocking systemic fixes; comprehensive fixes; recovery bundle
- Scenario integration tests; production integration tests (multiple versions)
- 15-bug-fix round

---

## v0.1.x — Prototype (vanilla)

### Phase 3: Frontend-backend integration (2026-07-11)
- Frontend productization & first real integration (Round 7)
- Roadmap finalized: integration-first, evidence-driven rewrite
- Backend phase A/B audits & work orders; E2E diagnostics & fixes
- Recovery fixes; approval-card-dead four-round resolution

### Phase 2: Backend Walking Skeleton (2026-07-10 ~ 07-11)
- Spike 1.x iterations (protocol compliance verification)
- M1–M5 milestones
- Walking Skeleton spec & reviews

### Phase 1: Design (2026-07-04 ~ 07-10)
- Harness engineering design v1 → v1.1 → v1.2 → v1.2.2
- MVP gap list & pre-dev work plan
- Spike1 protocol compliance plan

---

## Notes

- Early phases (v0.1/v0.2) summarized from delivery records (coarser granularity); v0.1 dates are exact from discussion-document timestamps; v0.2 early versions (v0.19/v0.20) are estimated ranges from delivery archives; v1.0 onward recorded per-commit with exact dates
- Versioning unified to three-part semver since v1.0.28 (single source: package.json)
