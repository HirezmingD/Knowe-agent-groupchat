# Changelog

**简体中文版：[CHANGELOG.zh-CN.md](./CHANGELOG.zh-CN.md)**

Complete evolution history of Knowe, from inception to the current release.

Version line: **v0.1 (prototype) → v0.2 (React rewrite) → v1.0 (productization & installer)**

---

## v1.0.x — Productization

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
