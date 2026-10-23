# Mota — Agentic PCB Demo (MOTA-DEMO-1.0)

A CLI-driven, native desktop circuit/PCB workspace with a Mota-owned engine. It turns a
verified circuit template, a short description, or an imported requirement document into an
editable schematic, a two-layer PCB with real routed copper, checks, a saved revision, and
re-openable project history.

This repository is the implementation of the MOTA-DEMO-1.0 contract. It is a demo, not a
production EDA tool and not a manufacturing approval system. `READY_FOR_REVIEW` means the
design is reviewable; it does not mean production ready.

## What works end to end

- Local accounts, per-user workspaces, session epochs and strict project isolation.
- Atomic revisions (`current.json` is the only commit point), drafts, undo/redo,
  revision restore, idempotency and revision conflicts.
- `power_3v3`, `led_timer`, `i2c_temperature` template builds with a real
  `CircuitIR → SchematicIR → BoardIR` pipeline.
- A two-layer grid router that emits real tracks and vias, plus independent copper
  connectivity, clearance, edge, width, via, NPTH-hole and courtyard checks.
- Native PySide6 workbench: Welcome, Auth, Build (Schematic / PCB 2D / PCB 3D), History,
  AI connections, Preferences and three-layer Memory. Compact and narrow layouts move the
  resources and agent into header-triggered overlays.
- Two provider protocols behind one frozen-per-run binding: OpenAI-style Responses and
  Chat Completions (DeepSeek included). Config schema v2 supports multiple connections and
  independent provider / API-format choices. Twelve strict tools; deterministic template
  builds do **not** require a model.
- Portable `.mota` bundles, SVG, BOM CSV and Markdown build reports.
- Three real sample projects in [`samples/`](samples/README.md).

## Install and run

Requires Python 3.11+ (this build was verified on Python 3.12.15) and macOS, Linux or
Windows with Qt support. The verified platform is macOS arm64.

```bash
cd /Users/danny/Documents/Mota
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
mota doctor
mota                 # opens the native desktop window
```

Or install the built wheel:

```bash
pip install dist/mota-1.0.0-py3-none-any.whl
mota doctor
mota desktop
```

`mota` and `mota desktop` start the Qt application and launch `mota engine --stdio` as a
child process. There is no HTTP server, browser, WebView or Electron shell.

### First run

1. `mota` → **Get started** → **Create account** (local only; no email verification).
2. Choose **LED Timer Board** or **I²C Temperature Board** on the empty Build page.
3. **Use template** → **Build this board**. The Agent panel shows real tool events.
4. Switch **Schematic / PCB 2D / PCB 3D**, open checks, export, then open **History**.

### Headless CLI examples

```bash
mota auth register --username demo --password 'Passw0rd!23' --confirm 'Passw0rd!23'
mota project create --name "Power board"
mota build --project <project_id> --template power_3v3 --accept-template-defaults
mota build --project <project_id> --template led_timer --set board_width_mm=55
mota project export <project_id> --format mota --output ./exports
mota project import samples/led_timer.mota --name "Imported LED timer"
mota run show <run_id>
mota memory list --layer experience --project <project_id>

# AI connections (config schema v2)
mota config show --json
mota config model set --profile deepseek-main --provider deepseek \
  --api-format chat_completions --model deepseek-flash --base-url https://api.deepseek.com
mota config api-key set --profile deepseek-main --session-only
mota config use deepseek-main
mota config probe-model --profile deepseek-main --suite text --suite tool_roundtrip
```

Probes send synthetic text and a generated image only; caps are text 1, tool roundtrip 2,
vision 1. A text success never implies a tool roundtrip success.

Add `--json` for stable machine output. Exit codes: `0` success, `2` input required,
`3` PARTIAL, `4` configuration/model unavailable, `5` input/constraint error, `6`
execution/save failure, `130` cancelled.

## Template status and honest results

| Template | Recipe status | Build result |
| --- | --- | --- |
| `led_timer` | verified (10 real regression runs) | `SUCCEEDED` / `READY_FOR_REVIEW` |
| `i2c_temperature` | verified (10 real regression runs) | `SUCCEEDED` / `READY_FOR_REVIEW` |
| `power_3v3` | candidate | `PARTIAL` / `REVIEW_BLOCKED` |

`power_3v3` is deliberately blocked: the demo catalog has no verified effective-capacitance
or ESR evidence for its output capacitor, so `OUTPUT_CAP_EFFECTIVE` and `OUTPUT_CAP_ESR`
are `NOT_CHECKED`. Required checks that are not `PASS` block review; the snapshot is still
saved as `REVIEW_BLOCKED`. This is exactly what the contract requires.

## Data layout

The data root is `platformdirs.user_data_dir("Mota")` or `MOTA_DATA_DIR`:

```text
<data>/
  app/accounts.json, cli_session.json, remember_tokens.json
  users/<user_id>/
    settings.json                 # versioned, redacted; no API key
    workspace/
      memory/{short_term,long_term,experience}/*.md
      projects/<project_id>/{project.json,current.json,commit_index.json,
                             revisions/<rev>/..., drafts/..., runs/...}
      tasks/...
```

Each project revision stores `circuit.json`, `schematic.json`, `board.json`, `checks.json`,
`scene/*.json`, `thumbnail.svg` and a manifest of SHA-256 hashes. Old revisions are never
mutated.

## Architecture

- **CLI** (`mota.cli.main`): product entry and headless control plane.
- **Engine** (`mota engine --stdio`): JSON-RPC 2.0, newline-delimited UTF-8, 4 MiB frames,
  stdout protocol only. Requests and events carry `session_epoch`.
- **ApplicationService**: one command surface for CLI and desktop.
- **Design pipeline**: `generation/modules.py` → `generation/schematic.py` →
  `generation/placement.py` → `generation/routing.py`.
- **Validation**: independent electrical, connectivity and geometric checks produce
  `PASS/FAIL/WARN/NOT_CHECKED`, bound to a `DesignVersion`.
- **Desktop**: PySide6 Widgets, QGraphicsView for 2D (shared world transform for cursor,
  rulers, grid and measure), PyQtGraph OpenGL for 3D with server-provided
  `world_matrix_4x4`.
- **Agent**: `agent/runner.py` bounded loop; `agent/tools.py` exposes exactly the 12 tools.

Important invariants:

- Geometry is stored as integer nanometres.
- A design version is exactly one of `revision_id` or `draft_id + draft_seq`.
- UI never creates its own circuit truth; it consumes `scene.get`, `validation.list` and
  artifact DTOs from one design version.
- `Saved` is shown only after a commit event/authoritative snapshot and a matching final
  scene.

## Known limitations (unchanged from the contract)

- 3D component bodies are **simplified/procedural** (`simplified` fidelity), not exact
  vendor STEP assets. Board holes are rendered as through-cylinders; the board mesh is not
  boolean-cut. A missing/approximate model count is shown in the 3D toolbar.
- KiCad export is implemented but **disabled**: `kicad-cli` is not installed in this
  environment, so compatibility is `NOT_VERIFIED`. The UI/CLI refuse the export with
  `COMPATIBILITY_UNVERIFIED`.
- No OpenAI API key was available in the build environment. Template builds are fully
  functional; the live model path and the Chinese-prompt extraction are `NOT_RUN`.
  The tool harness and clarification/resume flow were tested with a deterministic fake
  provider, which is clearly not a live-model claim.
- Official KiCad catalog download (`catalog.sync --source kicad`) is implemented but was
  not run against the network in acceptance. Local library import/parsing was tested with
  synthetic KiCad files. The built-in curated snapshot is the supported path.
- Only macOS arm64 was actually run. Windows/Linux are `NOT_RUN`.
- Thermal, EMC, external cable capacitance, physical LED lead polarity and production
  readiness are outside the verified scope and remain visible as non-blocking
  `WARN/NOT_CHECKED` items.
- No Gerber/drill/STEP assembly output; `glTF` export is disabled with an explicit reason.

## Verification and evidence

```bash
MOTA_DATA_DIR=/tmp/mota-test pytest -q
MOTA_DATA_DIR=/tmp/mota-regression python scripts/regression.py
QT_QPA_PLATFORM=cocoa python scripts/capture_ui.py
```

- [`docs/reports/final.md`](docs/reports/final.md) — acceptance matrix A01–A26 and U01–U15.
- [`docs/reports/stages.md`](docs/reports/stages.md) — M0–M10 stage reports.
- [`docs/reports/test-results.txt`](docs/reports/test-results.txt) — actual pytest output.
- [`docs/reports/regression.json`](docs/reports/regression.json) — 10 builds per template,
  run ids, revisions, canonical hashes and wall times.
- [`docs/screenshots/manifest.json`](docs/screenshots/manifest.json) — screenshot list and
  notes (one replay-only state is explicitly marked).

## Third-party and licensing

Mota application code is proprietary. Third-party packages and resources keep their
licenses; see [`third_party_manifest.json`](third_party_manifest.json) and
[`LICENSES/THIRD_PARTY.md`](LICENSES/THIRD_PARTY.md). The PyDracula and OpenBoardView
repositories were used as **design references only**; no source code was copied. KiCad
libraries are not redistributed in this repository.
