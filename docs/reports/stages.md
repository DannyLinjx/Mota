# M0–M10 — stage reports

Version: MOTA-DEMO-1.0 · Date: 2026-10-21 · Platform actually run: macOS 26.5.2 arm64,
Python 3.12.15, PySide6 6.12.0.

Rollback for every stage: the workspace is a local git-less directory; copies of the
previous tree can be restored from the delivery archive. Engineering data lives outside the
repository under `MOTA_DATA_DIR`, so re-installing the app never destroys user projects.
No stage mutated an existing revision in place; all changes create new revisions.

---

## M0 — Locked contracts and environment

**Outcome:** Completed.

**Changed files:** `pyproject.toml`, `src/mota/__init__.py`,
`src/mota/domain/{units,models,geometry}.py`, `src/mota/protocol/schemas.py`,
`src/mota/app/state.py`, `src/mota/errors.py`, `src/mota/workspace/{paths,atomic,locks}.py`.

**What was fixed:** one definition of `DesignVersion` (revision XOR draft+seq), RunState,
ReviewStatus, CheckStatus, TaskState, error codes, protocol v1 methods, 4 MiB frame limit,
session epoch, task event separation, integer-nanometre geometry.

**Commands actually executed:**

```bash
uv venv --python 3.12 .venv
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv pip install ... PySide6 PyOpenGL openai ...
uv pip install -e .
mota doctor --json
```

**Validation:** `mota doctor` reported Python, Qt, Qt Widgets, OpenGL widget module and a
writable data directory PASS; API key and optional `kicad-cli` FAIL (accurate, see M9).
`tests/unit/test_contract.py` verifies units, exclusive design version, canonical hash and
the parameter constraint gate.

**Failures / remaining:** no physical 200% monitor test; Qt scale factor simulation only.
`gpt-5.4` remains a configurable default, not a verified account capability.

---

## M1 — Users, workspace, project transactions

**Outcome:** Completed.

**Changed files:** `src/mota/auth/{service,credentials}.py`, `workspace/locks.py`,
`projects/store.py`, `app/{config,tasks,scenes}.py`, `protocol/stdio.py`.

**What was fixed:** Argon2id passwords, keychain/session-only secrets, per-user directories,
project file locks, atomic JSON, staging→revision→`current.json` commit point,
`commit_index.json`, idempotency, revision conflict, draft initialisation, recovery and
orphan-run recovery after engine restart.

**Commands executed:**

```bash
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_api_flow.py -q
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_io_and_tasks.py -q
```

**Validation:** `test_register_login_logout_and_isolation`, `test_commit_fault_keeps_current_and_reports_candidate`,
`test_engine_restart_marks_active_run_recoverable`, `test_run_idempotency_and_cancel_resume`,
`test_cross_user_artifact_access_is_denied` pass. Fault injection after `PREPARED` leaves
`current.json` unchanged; recovery does not switch current.

**Failures / remaining:** a crash exactly between current replacement and index COMMITTED
is repaired lazily by `recover_commits`; the shipped tests validate the PREPARED and
current-unchanged branches, not a filesystem power-loss test.

---

## M2 — Catalog, real pin mapping, rules and templates

**Outcome:** Completed for the curated snapshot.

**Changed files:** `catalog/{builtin,service,templates,kicad_parser,sync}.py`,
`resources/templates/*.yaml`, `resources/templates/*.verification.json`,
`resources/modules/*/module.yaml`, `resources/rules/*.yaml`,
`third_party_manifest.json`.

**Data:** 14 curated parts, 7 footprints, 8 symbols, 3 templates, 10 module descriptors,
5 rule files. MCP1700-3302E/TO, NE555P and DS1621+ pin maps are explicit; generic passives
are marked as Mota engineering specs, not vendor MPNs.

**Commands executed:**

```bash
mota catalog status
mota templates list
MOTA_DATA_DIR=/tmp/mota-regression python scripts/regression.py
```

**Validation:** `tests/unit/test_catalog.py` verifies real pinouts, capability diagnostics,
missing capacitor evidence, KiCad S-expression parsing and unsupported pad diagnostics.
`tests/integration/test_io_and_tasks.py::test_local_catalog_sync_with_unsupported_entries`
verifies a local library sync reports indexed and unsupported entries.

**Failures / remaining:** official KiCad network sync is implemented but not run; the
curated parts are the supported set. Capacitor derating evidence is genuinely absent, so
`power_3v3` remains `REVIEW_BLOCKED`.

---

## M3 — Schematic closed loop

**Outcome:** Completed.

**Changed files:** `domain/{circuit,schematic}.py`, `generation/{modules,schematic}.py`,
`validation/electrical.py`, `validation/reports.py`.

**What was fixed:** real components/nets/pins, explicit NC, schematic symbols with pin
anchors, stubs and unique net labels, optional clear Manhattan joins, junctions only at
real three-way points, template-specific electrical checks, review gate.

**Commands executed:**

```bash
MOTA_DATA_DIR=/tmp/mota-test pytest tests/unit/test_validation_negatives.py -q
mota build --project <id> --template power_3v3 --accept-template-defaults
```

**Validation:** power_3v3: 17 PASS, 0 FAIL, 4 NOT_CHECKED, REVIEW_BLOCKED; led/i2c:
20 PASS, 0 FAIL, 1 WARN, 1 NOT_CHECKED, READY_FOR_REVIEW. Negative tests cover missing
RESET, wrong timing, missing decoupling, reversed LED, floating pin, package mismatch and
duplicate references.

**Failures / remaining:** the schematic uses a label-based style for cross-module nets
(permitted by the contract), not a fully hand-drawn wire bus; local joins are drawn when
geometry is clear.

---

## M4 — PCB placement, routing and board checks

**Outcome:** Completed for small two-layer boards.

**Changed files:** `generation/{placement,routing,board}.py`, `domain/board.py`,
`validation/{geometry,connectivity}.py`, `validation/reports.py`.

**What was fixed:** module-rule placement with courtyard/hole/edge legalisation; 0.127 mm
grid A* with exact pad exits, vias, 30 s shared budget; real tracks/vias; independent
open/short/clearance/edge/width/via/NPTH/courtyard checks; copper connectivity graph.

**Commands executed:**

```bash
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_build_pipeline.py -q
MOTA_DATA_DIR=/tmp/mota-test pytest tests/unit/test_validation_negatives.py -q
```

**Validation:** all three templates route every net (`unrouted_nets == []`), DRC and
connectivity PASS. Negative cases: broken track, short, thin track, board-edge track,
copper through NPTH, cross-layer without via, sealed pad → explicit unrouted.

**Failures / remaining:** shrinking a board usually fails *placement* with
`CONSTRAINT_CONFLICT` before routing; the route-failure path is tested with an injected
router and a sealed-net fixture. Gate arrays, BGA fanout, high-speed and impedance rules
are out of scope.

---

## M5 — 2D/3D scenes and editing

**Outcome:** Completed with simplified 3D bodies.

**Changed files:** `render/{scene,meshes,svg}.py`, `app/scenes.py`,
`domain/patches.py` (typed operations in `domain/models.py`), `app/design.py`,
`desktop/widgets/{canvas_2d,rulers,board_3d,design_view}.py`.

**What was fixed:** authoritative scene DTOs per design version; shared component IDs;
integer nm → mm conversion; rulers/grid/cursor/measure share one transform; drag ghost
submits one patch; undo/redo create new revisions; server-side row-major
`world_matrix_4x4`; bottom-side matrix fixture; OpenGL fallback.

**Commands executed:**

```bash
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_build_pipeline.py -q
MOTA_DATA_DIR=/tmp/mota-test pytest tests/ui/test_desktop_contract.py -q
QT_QPA_PLATFORM=cocoa python scripts/capture_ui.py
```

**Validation:** `test_scene_views_share_component_ids`, `test_component_rotations_and_side_matrix`,
`test_canvas_world_to_screen_and_back_is_stable`, `test_ruler_steps_follow_one_two_five`.
Screenshots 06/07/08 show the same LED revision in all three views.

**Failures / remaining:** 3D bodies are `simplified`; no exact STEP assets; board mesh is
not boolean-cut for holes; 3D picking is projection-distance based, not a triangle BVH.

---

## M6 — Agent, documents and clarification

**Outcome:** Harness completed; live OpenAI path blocked (no key).

**Changed files:** `agent/{provider,runner,tools}.py`, `ingest/documents.py`,
`app/runs.py`, `app/service.py`.

**What was fixed:** Responses API adapter with streaming, 12 strict tools, 6-round/24-call
budgets, draft-seq validation, human-readable tool results, `ask_user` → `WAITING_FOR_USER`
→ `run.answer` on the same run, cancellation, MD/TXT/PDF/DOCX extraction with scanned-page
warnings.

**Commands executed:**

```bash
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_agent_contract.py -q
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_io_and_tasks.py -q
```

**Validation:** deterministic fake provider drives ask_user → prepare → validate → build →
validate → render → summarize and commits a real revision; duplicate answer is idempotent;
different answer is rejected; no-key start is blocked before creating a run. PDF blank
pages produce `needs_attention` and page warnings.

**Failures / remaining:** no `OPENAI_API_KEY` in the build environment, so A18 and live A04/
A05 are NOT_RUN. No claim is made that a model was called. The fake provider is test-only.

---

## M7 — Native desktop flow

**Outcome:** Completed for U01–U11, U14 and the History display part of U12.

**Changed files:** `desktop/{main,bridge,controllers}.py`, `desktop/pages/*`,
`desktop/widgets/*`, `desktop/theme/*`.

**What was fixed:** native window and page stack, Welcome animation + reduced motion, Auth,
three-pane Build, real agent cards, clarification card, 2D/3D canvases, Issues, template
choice, History cards with real thumbnails, Settings, Memory, keyboard shortcuts, compact
layout, engine restart banner, epoch filtering, stale-scene guards, final-revision loading.

**Commands executed:**

```bash
QT_QPA_PLATFORM=cocoa python scripts/capture_ui.py
QT_QPA_PLATFORM=cocoa QT_SCALE_FACTOR=1.5 python scripts/capture_ui_dpi.py
MOTA_DATA_DIR=/tmp/mota-test pytest tests/ui/test_desktop_contract.py -q
```

**Validation:** 17 live screenshots plus a 150% scale screenshot. The actual Qt app drove the
actual engine; timeline cards and commit order are real. Read-only revision shows
“Revision 2 · Read-only” in header, toolbar and status bar.

**Failures / remaining:** the clarification screenshot is a protocol-replay fixture and is
labelled as such; no live model question exists without a key. Physical 200% monitor and
Windows/Linux runs are NOT_RUN.

---

## M8 — Memory and history restore

**Outcome:** Completed.

**Changed files:** `memory/{markdown,service}.py`, `projects/{store,bundle}.py`,
`app/service.py`, `desktop/pages/{memory,history}.py`.

**What was fixed:** three Markdown layers with frontmatter and per-entry scope, owner/
scope/tombstone filtering before relevance ranking, hash-conflict detection, forget
tombstones persisted in the same layer, verified experience only from real check evidence,
revision restore as a new revision, immutable old revisions.

**Commands executed:**

```bash
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_memory.py -q
MOTA_DATA_DIR=/tmp/mota-test pytest tests/integration/test_api_flow.py -q
```

**Validation:** project-scope entries invisible in another project and to another user;
restart re-reads tombstones; forgotten semantic keys do not reappear; a restored revision
keeps `restored_from_revision_id` and a new revision id.

**Failures / remaining:** no embedding search (keyword/module matching only, as scoped in
the contract). UI edit conflict is handled, but there is no three-way diff editor.

---

## M9 — Independent validation and failure containment

**Outcome:** Partially completed; KiCad external validation blocked.

**Files:** `tests/**`, `adapters` not enabled, `docs/reports/{final,regression,test-results}.md/json/txt`.

**Commands executed:**

```bash
MOTA_DATA_DIR=/tmp/mota-test-final PYTHONPATH=src:tests .venv/bin/python -m pytest
MOTA_DATA_DIR=/tmp/mota-regression python scripts/regression.py
which kicad-cli   # not found
```

**Validation:** 83 tests pass (0 failures, 133 s). 10 real runs per template; per-board-variant
canonical hashes repeat. Median full build: power 406 ms, i2c 1.42 s, led 4.23 s.
Round 3 added config v1→v2 migration, frozen provider bindings, Chat Completions stream
aggregation (terminal usage chunk), probe request caps/evidence, and UI contracts for the
late-reply guard, History list/grid and template parameters.

**Failures / remaining:** no KiCad installation, so A17 is NOT_RUN and the export entry is
disabled. Network catalog sync not run. Live model acceptance not run. Windows/Linux not run.

---

## M10 — Packaging and delivery

**Outcome:** Completed for the source tree, wheel, CLI, samples and evidence.

**Files:** `dist/mota-1.0.0-py3-none-any.whl`, `samples/*`,
`docs/screenshots/*`, `docs/reports/*`, `README.md`, `LICENSES/THIRD_PARTY.md`,
`third_party_manifest.json`.

**Commands executed:**

```bash
uv build --wheel
python -c "import zipfile; ..."    # verified resources and verification sidecars in wheel
python scripts/make_samples.py
```

**Validation:** wheel contains templates, module/rule resources and verification sidecars;
sample bundles import into an empty data root offline (tested in `test_bundle_import_into_empty_root_offline`).

**Failures / remaining:** no signed `.app`/installer; the wheel + `mota` console script is
the runnable program on the verified platform. No Gerber/drill output.
