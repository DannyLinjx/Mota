# Mota — final acceptance report (MOTA-DEMO-1.0)

Date: 2026-10-21 · Verified platform: **macOS 26.5.2 arm64**, Python 3.12.15,
PySide6 6.12.0, Qt 6.12, pyqtgraph 0.14.0, pydantic 2.14.1, openai SDK 3.35.1.

This report separates **PASS**, **PARTIAL**, **BLOCKED** and **NOT_RUN**. Nothing here is
inferred from a design document: every PASS has a command, test or screenshot behind it.
`NOT_RUN` means the check was not executed in this environment; it is not a silent pass.

## 1. Delivered artifacts

| Artifact | Path |
| --- | --- |
| Runnable wheel + console script | `dist/mota-1.0.0-py3-none-any.whl`, `mota` |
| CLI + native desktop + engine | `src/mota/**` |
| Three real sample projects | `samples/{power_3v3,led_timer,i2c_temperature}.mota` and `*_sample/` |
| Regression evidence (30 real runs) | `docs/reports/regression.json` |
| Test output | `docs/reports/test-results.txt` (`83 tests, 0 failures`) |
| Stage reports M0–M10 | `docs/reports/stages.md` |
| Screenshots + notes | `docs/screenshots/manifest.json` (21 entries) |
| Licenses / third-party manifest | `LICENSES/THIRD_PARTY.md`, `third_party_manifest.json` |

## 2. What is actually implemented

- **Closed loop (no GUI):** account → project → template build → `CircuitIR` →
  `SchematicIR` → `BoardIR` → placement → two-layer routing → independent checks →
  atomic revision → reopen → export.
- **Real engineering data:** 5 components and 8 routed segments (power), 9 components and
  19 track segments + 2 vias (LED), 5 components and 13 segments (I²C).
- **Unified versions:** scenes, checks, BOM/report exports and History all bind to one
  `revision_id`, or one `draft_id + draft_seq` while building.
- **Native UI:** PySide6 Widgets, QGraphicsView 2D, PyQtGraph OpenGL 3D, no browser or
  WebView, no HTTP port.
- **Agent:** two provider protocols behind one binding — OpenAI-style Responses and Chat
  Completions (DeepSeek included) — with exactly 12 tools; the timeline is generated from
  real `tool.started/completed/failed` events.
- **AI connections:** config schema v2 with multiple provider profiles
  (`openai` / `deepseek` / `custom_openai_compatible`) and independent API formats
  (`responses` / `chat_completions`). Each run freezes a `ProviderBinding`; model turns
  record the returned model, request id and usage without rewriting the binding.
- **UI structure:** compact part rows with two-line delegates, template parameter preview,
  collapsible tool rows, “New activity”, History list/grid with a live preview pane, and
  header-triggered overlay panels below 1240 DIP.
- **Isolation:** per-user Argon2id accounts, owner-scoped project paths, session epochs,
  artifact permission checks, per-user Markdown memory.

## 3. Functional acceptance A01–A26

| ID | Status | Evidence / actual result | Remaining |
| --- | --- | --- | --- |
| A01 | **PASS** | `test_register_login_logout_and_isolation`; CLI register/login/logout on a fresh root; logout cancels active runs and requires auth again. | — |
| A02 | **PASS** | Same test: A and B see only their own projects; B opening A's id returns `PROJECT_NOT_FOUND`; `test_cross_user_artifact_access_is_denied`; `test_run_controller_discards_old_epoch_and_duplicate_sequences`. | UI epoch is unit-tested; a full two-user live GUI switch is not screenshot-recorded. |
| A03 | **PASS** | `led_timer` and `i2c_temperature` build `SUCCEEDED/READY_FOR_REVIEW`; `power_3v3` saves `PARTIAL/REVIEW_BLOCKED`; all three views share component ids and one revision. | `power_3v3` is blocked by missing capacitor evidence by design (see A22). |
| A04 | **NOT_RUN** | Chinese input is accepted and preserved, but requirement extraction is a model task and no API key exists. | Live model extraction not performed; no fabricated extraction result. |
| A05 | **PASS (harness) / NOT_RUN (live)** | Fake-provider test: `ask_user` → `WAITING_FOR_USER` → `run.answer` on the same run; duplicate answer replay; different answer rejected (`QUESTION_CONSUMED`). | Live model wording/decision quality not measured. |
| A06 | **PASS** | `test_source_import_markdown_text_pdf_and_docx`: MD sections, TXT, DOCX paragraphs/tables, PDF page count and scanned-page warning (`needs_attention`). | Image-only pages require a vision model; not run. |
| A07 | **PASS** | `run.start` rejects 12 V with `CONSTRAINT_CONFLICT` before creating a run; `test_constraint_rejects_out_of_range_voltage`; no revision is created. | — |
| A08 | **PASS** | `test_same_series_package_change_requires_reverification`: U1 footprint changed to DIP-8 → `PIN_PAD_MAPPING=FAIL` with “does not match … pin map must be re-verified”. | Only the MCP1700 package variants are modelled. |
| A09 | **PASS** | `test_edit_conflict_undo_redo_and_busy`: move → `COMMITTED` revision, review status recomputed; stale expected revision → `REVISION_CONFLICT`; undo/redo create new revisions; UI loads the matching scene after `COMMITTED`. | Physical drag not recorded as a video; the handler is exercised in the app. |
| A10 | **PASS** | `test_route_failure_commits_partial_without_green_checks`: injected router failure → run `PARTIAL`, revision saved `REVIEW_BLOCKED`, `ROUTING_COMPLETE` and `COPPER_CONNECTIVITY` `FAIL`, ratsnest/unrouted evidence retained; sealed-pad router test. | Shrinking a board usually stops earlier at placement with `CONSTRAINT_CONFLICT`; that is reported, not turned green. |
| A11 | **PASS (tested paths)** | Cancel before commit and resume on checkpoint; commit fault after `PREPARED` leaves `current.json` unchanged and creates no false save; restart marks an orphan run `RECOVERABLE` and creates a new child run. | A power-loss test on the filesystem is not performed. |
| A12 | **PASS** | History list from ProjectStore; `revision.open` read-only; `revision.restore` creates a new revision with `restored_from_revision_id`; current never changed by viewing. Screenshot 12/13. | — |
| A13 | **PASS** | `test_project_data_survives_process_restart`: a new `ApplicationService` on the same root logs in, opens the project, and serves the saved PCB scene. | A cloud conversation is intentionally not used. |
| A14 | **PASS** | `test_update_hash_conflict_and_forget_tombstone_survive_restart`: forgotten entry disappears, tombstone persists after restart, replacement with the same semantic key stays suppressed; other user unaffected. | Re-confirmation UI is not implemented; the API contract for new confirmation exists via new entries. |
| A15 | **PASS (local)** | `sync_local_tree` reports `indexed`, `unsupported` and per-file diagnostics for a broken symbol library; `catalog.inspect` reports capability diagnostics. | Official full-library network sync was not run. |
| A16 | **PASS** | `kicad-cli`, `kicad`, `eeschema`, `pcbnew` are absent (`doctor`); all three builds, checks, saves and reopens work; the engine never launches KiCad. | — |
| A17 | **NOT_RUN** | No KiCad installation to parse/ERC/DRC/export-check. `project.export --format kicad` returns `COMPATIBILITY_UNVERIFIED`; the UI entry is disabled. | Blocked by missing external tool; export not claimed as compatible. |
| A18 | **BLOCKED / NOT_RUN (live)** | No `OPENAI_API_KEY` / `MOTA_OPENAI_API_KEY` / `DEEPSEEK_API_KEY` in the environment. The live path fails with `MODEL_UNAVAILABLE`; template builds remain available. The adapter contract (both `responses` and `chat_completions`, streaming tool-argument aggregation, terminal usage chunk, truncation guard) is covered by tests with a fake client. | A real request, token count and returned model cannot be reported. |
| A19 | **PASS (partial)** | 2D world→scene→screen round-trip stable within one pixel; ruler 1–2–5 steps; row-major bottom-side matrix fixture; 150% Qt scale screenshot. | Physical 200% monitor and a second GPU were not available; 3D picking is projection-based. |
| A20 | **PASS (partial)** | Built-in snapshot id is pinned in revisions; local catalog sync writes a new snapshot without touching existing projects. | Official snapshot update was not run; no old official project exists to reopen. |
| A21 | **PASS** | `test_draft_is_initialised_once_and_stale_seq_is_rejected`; fake-agent run performs prepare→validate→build→validate→render with monotonic seq; stale seq → `DRAFT_CONFLICT`; active run → `PROJECT_BUSY`. | — |
| A22 | **PASS** | `power_3v3` has `OUTPUT_CAP_EFFECTIVE` and `OUTPUT_CAP_ESR` `NOT_CHECKED`, both `required_for_review=true`; review is `REVIEW_BLOCKED`; EMC/thermal remain visible but non-blocking. | Providing real capacitor evidence would enable the checks, but no such evidence exists here. |
| A23 | **PASS (built-in/probe harness)** | `test_catalog_sync_task_and_model_probe_task`: independent `task_id`, `task.started/progress/completed`, `task.inspect`, probe bound to `config_version`, redacted key, session-only storage reported. `test_probe_caps_and_evidence_are_per_suite` proves per-suite request caps and evidence; `test_text_success_does_not_imply_a_tool_roundtrip` proves text cannot fake a tool loop; DeepSeek Pro vision reports `UNSUPPORTED` without a request. | Live probe not run (no key); official network sync not run. |
| A24 | **PASS** | `test_bundle_import_into_empty_root_offline`: export → new data root → import → new `project_id` → schematic/PCB/3D scenes reopen; traversal, tampered hash rejected; repeated import idempotent. | KiCad files are intentionally not in the bundle. |
| A25 | **PASS** | `test_experience_scope_is_project_only_and_isolated_between_users`: project-scope experience invisible in a second project and to another user; user-scope preference visible across projects. | — |
| A26 | **PASS** | `test_artifact_chunking_and_tamper_detection`: 64-byte chunk + `next_offset`, >1 MiB rejected, tampered SHA rejected; cross-user access denied; late draft scenes discarded by version guards. | — |

### Independent negative cases

Covered by tests: wrong pin map, missing decoupling, floating RESET, LED polarity reversal,
short between nets, broken track endpoint, cross-layer without via, copper through NPTH,
clearance violation, board-edge violation, invalid asset, duplicate reference, stale
revision, stale draft seq, idempotency conflict, active-run edit, other user's project/
artifact, bundle path traversal/hash mismatch, forgotten fact re-extraction, router sealed
pad, route failure. Model half-JSON handling exists in the runner (tool argument parse
failure becomes a tool diagnostics result) but is not exercised by a live model.

## 4. UI acceptance U01–U15

| ID | Status | Evidence |
| --- | --- | --- |
| U01 | **PASS** | `mota` / `mota desktop` starts the Qt window and `mota engine --stdio`; no HTTP/browser. Screenshots 01–17. |
| U02 | **PASS** | Welcome slide/fade and clickable CTA during animation; `02_welcome_reduced_motion.png` shows the reduced-motion state (no trace loop). |
| U03 | **PASS** | Left sample animation, right local form, real register/login/logout; `03_auth_sign_in.png`, `04_auth_error.png`; no email/code flow. |
| U04 | **PASS (evidence at 1440, 1280, 1180, 1020, 150%)** | `16_build_1280x800.png` (resources + centre + agent), `17_build_compact.png` (resources and agent behind header toggles), `20_build_narrow_agent_overlay.png` (agent overlay with composer and draft), `18_build_150pct.png`. A physical 1920×1080@150% availableGeometry run is NOT_RUN. |
| U05 | **PASS** | Empty Build cards come from `template.list`; template status is verified for LED/I²C and candidate for power; catalog search distinguishes “No matching parts” from “Catalog unavailable”. Screenshot 05. |
| U06 | **PASS (transform + 150%)** | Canvas transform round-trip test; rulers/grid/cursor share it; `18_build_150pct.png` shows unclipped text and ruler. Physical 200% is NOT_RUN. |
| U07 | **PASS (partial)** | Three scenes share component IDs (`test_scene_views_share_component_ids`); bottom-side matrix fixture; 3D toolbar shows “9 approximate model(s)”; screenshot 08. Exact vendor models are not bundled. |
| U08 | **PASS** | Screenshot 09 and the real run events: tool started/completed, patch, validation, commit, result card; no scripted “Completed”. |
| U09 | **PASS (harness + replay UI)** | Real ask_user/resume tested with the fake provider; UI clarification state captured as `11_clarification_replay.png` and explicitly labelled a protocol-replay fixture, not a live model run. |
| U10 | **PASS (service + UI guards)** | Cancel/resume, commit-before/after rules, late-scene guards, “Revision saved · Loading final view” until the final revision scene arrives; recovery after restart. |
| U11 | **PASS** | One drag → one patch (ghost is local); active run disables writes with `PROJECT_BUSY`; undo/redo create revisions; read-only revision cannot be edited. |
| U12 | **PASS** | History defaults to a compact list with a live preview pane and a List/Grid switch; real thumbnails, revision list, read-only revision view, restore as new revision, archive/duplicate/delete semantics. Screenshots 12/13/19. |
| U13 | **PASS** | Memory page shows scope (“Only this project”), source/evidence, path, edit with hash conflict, forget tombstone; A/B isolation tested. Screenshot 15. |
| U14 | **PASS (exercised reasons)** | No key → explicit error + “AI connections”; route failure; save fault; no-OpenGL fallback widget for unsupported display; disabled KiCad export explains why; probe failures show the suite, request count and message without turning the row green. |
| U15 | **PASS (template path, macOS)** | Login → template preview → build → checks → save → History list → export → offline import into empty data root → reopen three views. Live prompt path and KiCad export are excluded and marked NOT_RUN. |

Screenshots are in [`docs/screenshots/`](screenshots/) with notes in `manifest.json`
(21 live UI captures). The 150% Qt-scale capture is recorded separately in
`18_build_150pct.png` / `18_build_150pct.json`.
The 3D window screenshot composites the real GL framebuffer into the Qt window grab; macOS
`QWidget.grab()` alone does not include the OpenGL child surface.

## 5. Real regression and determinism

`scripts/regression.py` performed 10 real runs per template (30 total), each in a new
project, with three board-size variants:

| Template | Runs | States | Median wall | Max wall | Canonical hash per variant |
| --- | ---: | --- | ---: | ---: | --- |
| power_3v3 | 10 | 10 PARTIAL / REVIEW_BLOCKED | 406 ms | 449 ms | stable (1 value/variant) |
| led_timer | 10 | 10 SUCCEEDED / READY_FOR_REVIEW | 4233 ms | 4429 ms | stable (1 value/variant) |
| i2c_temperature | 10 | 10 SUCCEEDED / READY_FOR_REVIEW | 1421 ms | 1544 ms | stable (1 value/variant) |

Verified run ids are recorded in `src/mota/resources/templates/*.verification.json`.
power_3v3 intentionally has an empty `validated_run_ids` list because not all required
checks pass; its recipe stays `candidate`.

Performance targets from the contract: template complete build ≤30 s (measured max 4.43 s,
warm, excluding first downloads/STEP conversion). First schematic ≤10 s warm (not separately
instrumented; the first scene is emitted within the same sub-5-second run). Desktop warm
start, 2D/3D FPS, cancellation worker latency and 3D triangle counts are **not measured**
and are not claimed.

## 6. Commands actually run (representative)

```bash
uv venv --python 3.12 .venv
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv pip install ...
uv pip install -e .
MOTA_DATA_DIR=/tmp/mota-test-final PYTHONPATH=src:tests .venv/bin/python -m pytest
MOTA_DATA_DIR=/tmp/mota-regression python scripts/regression.py
MOTA_DATA_DIR=/tmp/mota-capture-data QT_QPA_PLATFORM=cocoa python scripts/capture_ui.py
QT_QPA_PLATFORM=cocoa QT_SCALE_FACTOR=1.5 python scripts/capture_ui_dpi.py
uv build --wheel
python scripts/make_samples.py
mota doctor --json
mota build --project <id> --template led_timer --accept-template-defaults
mota project export <id> --format mota --output /tmp/exports
```

Exact test output: `83 tests · 0 failures · 133.49 s` (`docs/reports/test-results.txt`).
Doctor: Python/Qt/Qt Widgets/OpenGL module/data dir/catalog PASS; API key FAIL;
`kicad-cli` FAIL (optional); overall `ok: true`.

## 7. Failures, blocks and things not claimed

1. **Live OpenAI path (A04, A05 live, A18, A23 live probe): blocked by missing API key.**
   No model request, token count or model-selected part is claimed. Template builds and the
   deterministic tool harness remain fully tested.
2. **KiCad compatibility (A17): NOT_RUN.** No KiCad executable is installed. The writer is
   implemented but the export entry is disabled and the CLI returns
   `COMPATIBILITY_UNVERIFIED`.
3. **Exact 3D assets:** all bodies are `simplified`; the board mesh is not boolean-cut for
   holes. This is displayed, not hidden.
4. **Official catalog download (A20/A15 network): NOT_RUN.** Local KiCad parsing is tested;
   the curated bundle is the supported path.
5. **Platform matrix:** only macOS arm64 was run. Windows/Linux are NOT_RUN.
6. **Physical display matrix:** 150% Qt scale is captured; 100% is the base; physical 200%
   and a second GPU were not available.
7. **Power board review:** `power_3v3` is saved but `REVIEW_BLOCKED` because two required
   capacitor checks have no evidence. This is the contractually correct outcome, not a test
   failure.
8. **Not implemented / out of scope:** Gerber/drill/STEP assembly, glTF export, zone fills,
   high-speed/RF/BGA, thermal/EMC sign-off, production ordering, cloud sync, multi-user
   collaboration, and exact KiCad ERC/DRC.

## 8. Security, privacy and data handling

- Passwords are Argon2id hashes; API keys use the OS keychain or explicit session-only
  storage. `config.get` returns only `api_key_configured` and `secret_storage`.
- No API key, password or token appears in events, checkpoints, logs, screenshots, samples
  or reports.
- Each project path is resolved from the authenticated `user_id`; `..`, absolute paths,
  symlinks and bundle traversal are rejected.
- Artifacts are resolved by id inside the owner's manifest, with SHA-256 validation and
  1 MiB chunk limits; arbitrary paths are never accepted.
- Memory files are local Markdown under the user's workspace; forgotten facts are
  tombstoned in the same layer and filtered before retrieval.
- The UI states that selected requirement text, project summaries and memory excerpts are
  sent to the model when a description is used. Template builds send nothing.

## 9. Known product limitations (explicitly visible in the UI)

- The schematic uses label-based cross-module connectivity with local wire joins, not a
  fully hand-routed wire bus.
- 3D selection is a screen-projection proximity pick; there is no triangle BVH.
- The 3D toolbar summarizes approximate models by count; per-object fidelity is available
  on selection and in the scene DTO.
- Per-layer visibility toggles are not implemented; the 2D canvas always draws the real
  F.Cu / B.Cu / silkscreen objects from the loaded scene, so no layer is faked or hidden
  silently.
- Board resize re-places only template-origin components; user-locked components keep their
  coordinates and may make a small board unplaceable, which is reported as
  `CONSTRAINT_CONFLICT`.
- `power_3v3` remains review-blocked until capacitor derating/ESR evidence is supplied.

## 10. Reproduce the acceptance run

```bash
cd /Users/danny/Documents/Mota
source .venv/bin/activate
mota doctor
MOTA_DATA_DIR=/tmp/mota-test-final pytest
MOTA_DATA_DIR=/tmp/mota-regression python scripts/regression.py
QT_QPA_PLATFORM=cocoa python scripts/capture_ui.py
mota project import samples/led_timer.mota --name "Imported LED timer"
```

For a clean user-data proof, delete the target `MOTA_DATA_DIR`, start `mota`, register a new
local account, build a template, close the app, reopen it, and open History. This was run
with `/tmp/mota-capture-data` and `/tmp/cli_demo` during acceptance.

## 11. Post-acceptance auth UI fix (2026-10-21)

A real UI defect was found while re-testing registration/login:

- `AuthPage.set_busy()` changed only the active form's button, and on the register form it
  left the label reading “Signing in…” after the request completed. The login-form switch
  also remained enabled while a request was in flight, and duplicate Enter/click
  submissions were possible.
- Fixed in `desktop/pages/auth.py` and `desktop/main.py`: both buttons now receive the
  correct mode-specific label (“Signing in…” / “Creating account…”), both are disabled while
  busy, form switching is blocked during a request, duplicate submissions are ignored, the
  error is routed to the form that actually submitted it, and labels reset after
  registration/logout.
- Added `test_auth_busy_state_uses_the_right_form`; the suite reached 67 tests at that point
  (83 in section 12).
- A local account `admin` (display name `Admin`) was created in the default data root
  (`~/Library/Application Support/Mota`) with the password requested by the user. The
  password is not reproduced in this report. The CLI session on this machine is signed in
  as that account; the desktop app can sign in with the same credentials.

## 12. Round 3 — UI polish and generic AI connections (2026-10-21)

Scope: make the workspace feel softer and more legible, and replace the single model
setting with explicit AI connections that separate **provider** from **API format**.

### 12.1 What was completed

- **Design tokens:** one dark mint palette, 8-DIP spacing scale, 9/13 px radii, hover /
  focus / pressed / disabled states for buttons, focus rings on inputs, round scrollbars,
  chips for metadata, and a mono face for engineering numbers.
- **Build workspace:** 1440×900 keeps 252 / 828 / 352 with 4 px handles. Below 1240 DIP the
  resources move into a header-triggered overlay; below 1200 DIP the agent moves into a
  right-side overlay that keeps the composer and any unsent draft. Overlays open below the
  view tabs, so tabs and the view toolbar stay reachable.
- **Components:** two-line delegate rows (56 DIP; 40 DIP in compact mode), stable
  positions for MPN / package / capability labels, an inspector with symbol, footprint,
  pins, sources and limits, and request-identity guards so a late A reply cannot overwrite
  a newer B selection.
- **Templates:** *Use template* only opens the parameter preview. A project is created and
  `run.start` is issued only after **Build this board**, with the confirmed parameters.
- **Agent:** user / assistant / tool rows are visually distinct; tool rows collapse on
  success and expand on failure, show real affected object ids as chips (U1, R1 …) and
  select that real id in the current view, and keep historical steps compact. A
  *New activity* control appears when you read older messages and the scroll position is
  not stolen. The input area stays docked.
- **History:** compact list by default with a live preview pane, plus a List/Grid switch.
  Selection shows the real saved revision thumbnail and the same revision facts as the
  card; historical revisions remain read-only in Version details.
- **Auth:** the form column is 360 DIP wide; the sample board is labelled *Sample circuit*.
  Register/sign-in busy states were already corrected in section 11.
- **Welcome:** wordmark, subtitle and CTA share one vertical axis; edge traces settle after
  one pass instead of looping forever.
- **AI connections:** left profile list, right Connection / Model behavior / Advanced form
  with labels above each field, capability suites underneath, and three separate actions —
  *Save connection*, *Test connection*, *Set as default for new runs*.
- **Config v2:** `schema_version=2`, multiple profiles, `default_profile_id`, per-profile
  keys under `api_key:<profile_id>`, and an automatic v1→v2 migration that preserves the
  legacy key's storage class (keychain vs session-only).
- **Protocols:** `ProviderAdapter` with `ResponsesAdapter` and `ChatCompletionsAdapter`.
  Chat streaming aggregates `tool_calls[].index` fragments, requests
  `stream_options.include_usage`, and keeps the terminal usage chunk. DeepSeek thinking is
  mapped through `extra_body.thinking` (`disabled` / `auto` / `enabled` + `low|high|max`);
  raw reasoning never leaves adapter-private state.
- **Run contract:** `run.start` freezes a `ProviderBinding`; `model.turn` records the
  returned model, request id, usage and finish reason. Switching the default affects new
  runs only. Truncated or half-JSON tool arguments cannot reach the project.
- **Probes:** `text`, `tool_roundtrip`, `vision` only; synthetic text and a generated 8×8
  image; request caps 1/2/1; DeepSeek `pro`/`reasoner` vision reported `UNSUPPORTED`
  without a request.
- **CLI:** `config model set --profile/--api-format`, `config api-key set --profile`,
  `config use <profile>`, `config show` lists profiles, and
  `config probe-model --suite ... --profile ...`.

### 12.2 Files changed in this round

```
src/mota/domain/models.py              ProviderProfile, ProviderBinding, config v2 DTOs
src/mota/app/config.py                 v2 storage, migration, profiles, probe results
src/mota/auth/credentials.py           per-profile keys, storage class fix
src/mota/agent/provider.py             Responses + Chat Completions adapters
src/mota/agent/probe.py                capped, side-effect-free capability probes
src/mota/agent/runner.py               binding snapshot, model.turn, truncation guard
src/mota/agent/tools.py                affected ids + references for locate
src/mota/app/runs.py                   freeze binding on run.start, return it
src/mota/app/service.py                config.probe_model suites/profile
src/mota/cli/main.py                   profile/format/suite options
src/mota/desktop/theme/tokens.py       palette, radii, spacing, states
src/mota/desktop/theme/motion.py       GL-safe page cross-fade
src/mota/desktop/pages/build.py        template parameters, overlays, agent wiring
src/mota/desktop/pages/settings.py     AI connections page
src/mota/desktop/pages/history.py      list/grid + preview pane
src/mota/desktop/pages/workspace.py    layout-mode buttons, reduced motion
src/mota/desktop/pages/auth.py         360 DIP column, sample label
src/mota/desktop/pages/welcome.py      vertical axis, one-pass background
src/mota/desktop/widgets/resource_panel.py   delegate, async identity, inspector
src/mota/desktop/widgets/agent_panel.py      new activity, sticky scroll, model label
src/mota/desktop/widgets/action_card.py      collapsible tool rows, short titles
src/mota/desktop/controllers.py              probe(profile, suites)
scripts/capture_ui.py                         new screenshots 18/19/20
tests/integration/test_config_v2.py           migration, profiles, secret isolation
tests/integration/test_provider_v2.py         Chat stream, thinking, probe caps
tests/integration/test_agent_contract.py      binding + model.turn assertions
tests/integration/test_io_and_tasks.py        probe task via the new contract
tests/ui/test_desktop_contract.py             late reply, History, template params, rows
```

### 12.3 Verification actually performed

| Check | Result |
| --- | --- |
| Full test suite against editable source | **83 tests, 0 failures, 133.49 s** |
| 30 real template builds (10 × 3 variants each) | all terminal states as expected; canonical hash stable per variant; max 4.43 s |
| UI screenshots from the live app | 21 captures in `manifest.json`, including the template parameter preview, compact and narrow overlays, History list/grid, and the DeepSeek connection |
| Chat Completions stream | incremental tool arguments, terminal usage chunk and request id asserted |
| Truncation / half JSON | runner raises before any tool runs; invalid JSON becomes a tool diagnostic, no project change |
| Config migration | v1 file + legacy session-only key → v2 profile, preferences preserved, key never in JSON |
| Default switch | frozen binding keeps its provider/model; new config only changes the default id |
| Probe caps | text 1, tool roundtrip 2, vision 1; text PASS does not make tool roundtrip PASS |
| Secret isolation | key absent from `settings.json`, `config.get`, events and screenshots |

### 12.4 NOT_RUN / remaining

- **Live OpenAI, DeepSeek and custom endpoint requests:** no API key in this environment.
  The DeepSeek profile (`deepseek-main`, `deepseek-flash`, `chat_completions`) is saved and
  visible in AI connections, but no real model turn, token count, returned model or vision
  result is claimed. Marked `NOT_RUN`.
- **DeepSeek Responses path:** implemented as an independent `responses` path and selected
  by `api_format`; only adapter-level behaviour is tested, no live request.
- **Strict tool schema:** the switch only requests strict schemas; a “strict passed” claim
  requires the `tool_roundtrip` suite, which is `NOT_RUN` without a key.
- **Physical 200% display / Windows / Linux:** not available; 150% Qt scale is captured.
- **KiCad compatibility:** unchanged, `NOT_RUN` (no executable).

