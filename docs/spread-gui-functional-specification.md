# SPREAD GUI – Functional Specification

| Field          | Value                                         |
|----------------|-----------------------------------------------|
| Document owner | Armin Wagner                                  |
| Application    | SPREAD Processing Pipeline GUI                |
| Status         | Current implementation                        |
| Last updated   | May 2026                                      |
| Audience       | Beamline scientists, software developers      |

---

## 1. Purpose

This document describes the current implemented functionality of the SPREAD Processing Pipeline GUI. It is intended to support operational use on the I23 beamline, software maintenance, and technical review.

---

## 2. Scope

**In scope**

- User-visible GUI behaviour
- Project and crystal management (database)
- Processing parameter configuration, script generation, and job submission
- xia2 and autoPROC output analysis and report generation
- phenix.refine anomalous refinement submission and f′/f″ analysis
- Platform and operational constraints

**Out of scope**

- Algorithmic details of SPREAD itself
- Future or proposed features not yet implemented

---

## 3. Intended Users

- Beamline scientists and users configuring and submitting SPREAD processing jobs
- Scientific software developers maintaining or extending the GUI
- Support staff diagnosing operational issues

Users are assumed to be familiar with Diamond visit structure, SLURM job submission, and crystallographic terminology (space group, unit cell, energies, wedges).

---

## 4. System Overview

The SPREAD GUI is a PyQt6 desktop application that provides a structured interface to:

- Organise data collections into projects and crystals with a persistent SQLite database
- Define processing parameters (energies, wedges, pipeline, paths)
- Generate SLURM-compatible job scripts for the selected pipeline only
- Submit jobs to the Diamond compute cluster via REST API, or generate scripts for manual submission on Wilson
- Track submitted jobs and monitor completion via filesystem polling
- Analyse xia2 and autoPROC output across energy points and generate an HTML report with plots
- Submit phenix.refine anomalous refinement jobs across the energy/wedge grid and analyse the resulting f′/f″ values as a function of energy

The application is designed for interactive use on Diamond Linux workstations and via remote NX sessions.

---

## 5. Software Architecture

### 5.1 Entry Point

`spread_gui.app.main()` initialises `QApplication` and instantiates `MainWindow`.

### 5.2 Major Components

| Component              | Responsibility                                                                                   |
|------------------------|--------------------------------------------------------------------------------------------------|
| `MainWindow`           | Application shell, header bar, tab container, shared log panel, status bar, job polling timer    |
| `ProcessingTab`        | Energy/wedge/pipeline configuration, script generation, submission; embeds `AnalysisTab`         |
| `AnalysisTab`          | xia2 and autoPROC result parsing and HTML report generation ("Analyse Processing" section)       |
| `SpreadTab`            | Phenix anomalous refinement submission and f′/f″ analysis ("SPREAD" tab)                        |
| `ManageProjectsDialog` | Two-pane project/crystal browser; crystal creation and deletion                                  |
| `SgCellDialog`         | Space group and unit cell definition (three input methods)                                       |
| `ProjectDB`            | SQLite-backed persistence for projects, crystals, settings, and submitted jobs                   |
| `xia2_parser`          | Parse xia2.txt statistics tables                                                                 |
| `xia2_analysis`        | Scan results, generate plots and HTML report for xia2                                            |
| `autoproc_parser`      | Parse aP.log STARANISO summary block                                                             |
| `autoproc_analysis`    | Scan results, generate plots and HTML report for autoPROC                                        |
| `phenix_parser`        | Parse phenix.refine log files; collect f′/f″ per anomalous group across the energy/wedge grid   |
| `phenix_analysis`      | Generate self-contained HTML report with f′/f″ vs energy plots and R-work/R-free tables         |

### 5.3 Layout

- The application has two top-level tabs: **Processing** and **SPREAD**.
- Both tabs are embedded in `QScrollArea` widgets for usability on small displays.
- A **shared log panel** is permanently visible below the tabs, separated by a `QSplitter`. It shows messages from both tabs and has Clear and Save log buttons.
- `MainWindow` carries a persistent header bar visible on all tabs.
- SSH check and other slow operations are deferred via `QTimer.singleShot(0, …)` so the window paints before blocking.

---

## 6. Persistent Storage

### 6.1 SQLite Database

Location: `~/.config/spread_gui/projects.db`

Schema:

```
projects  (id, name, created_at)

crystals  (id, project_id, name, visit, data_path, proc_path,
           settings TEXT,   -- JSON blob of all form fields
           created_at, updated_at)

jobs      (id, crystal_id, slurm_job_id, pipeline, proc_dir, output_dir,
           energy_ev, cumulative, dry_run, status, submitted_at, updated_at)
```

- Crystals are deleted automatically when their parent project is deleted (`ON DELETE CASCADE`); jobs cascade from crystals.
- The `settings` JSON uses the same key names as `settings.ini` so either source can populate the form without conversion.
- `jobs.output_dir` stores the path of the pipeline output directory **relative to `proc_dir`** (e.g. `7118eV/300img/xia2-dials`). This allows a future cleanup feature to identify and selectively delete output files per job and pipeline without ambiguity.
- `jobs.status` is one of `submitted`, `completed`, or `failed`. The filesystem poller updates this to `completed` when the output directory appears on disk.
- Dry-run submissions are recorded with `dry_run=1` and are excluded from the tab indicator.

### 6.2 INI Settings File

Location: `~/.config/spread_gui/settings.ini`

Stores the same flat key-value dict as the DB settings blob. Used to restore the last-used crystal and form state on startup. Auto-saved one second after the last field change.

---

## 7. Header Bar

Displayed above all tabs; updated automatically whenever a crystal is loaded or any field changes.

| Field         | Description                                   |
|---------------|-----------------------------------------------|
| Project       | Name of the currently loaded project          |
| Crystal       | Name of the currently loaded crystal          |
| Data path     | Path to raw data (selectable, not editable)   |
| Proc path     | Path to processing output (selectable)        |
| Space group   | Hermann–Mauguin symbol                        |
| Unit cell     | a, b, c, α, β, γ                             |

All fields show `—` when no crystal is loaded or the value is not yet defined.

A **Manage Projects…** button in the top-right opens the project/crystal browser.

---

## 8. Project and Crystal Management

### 8.1 Manage Projects Dialog

A two-pane browser (minimum 1000 × 450 px):

- **Left pane** – all projects, alphabetically sorted. A bullet (●) marks the project of the currently loaded crystal.
- **Right pane** – crystals within the selected project. A bullet marks the currently loaded crystal.

Actions available:

| Button            | Behaviour                                                                 |
|-------------------|---------------------------------------------------------------------------|
| + New (project)   | Prompts for a name; creates and selects the new project                   |
| ✕ Delete (project)| Confirms deletion; cascades to all crystals in that project              |
| + New (crystal)   | Opens the New Crystal sub-dialog; pre-fills from current form state       |
| ✕ Delete (crystal)| Confirms deletion                                                         |
| Set SG & Cell…    | Opens `SgCellDialog` for the selected crystal; enabled when a crystal is selected |
| Load Crystal      | Confirms, saves current state, loads the selected crystal                 |
| Close             | Closes without loading                                                    |

Double-clicking a crystal row is equivalent to **Load Crystal**.

When **Load Crystal** is accepted, the caller saves the current settings first, then calls `load_crystal()` which applies the stored settings, syncs the INI file, and emits `crystal_context_changed` and `processing_info_changed` signals.

If **Set SG & Cell…** was used for the currently loaded crystal, the dialog sets an internal `_needs_reload` flag so `MainWindow` reloads the crystal automatically on close, without requiring an explicit Load action.

### 8.2 New Crystal Dialog

Fields: crystal name, visit, data path, processing path (with Browse buttons for paths). Pre-filled from the current form state where applicable.

### 8.3 Space Group and Unit Cell – SgCellDialog

Three input methods selectable via radio buttons:

| Method        | Behaviour                                                                                   |
|---------------|---------------------------------------------------------------------------------------------|
| Upload PDB file | Browse to a local `.pdb` file; click **Load** to parse the CRYST1 record; preview shown  |
| PDB code      | Enter a 4-character code; click **Fetch** to download from RCSB and parse CRYST1; preview shown |
| Manual        | Select space group from a dropdown of all 230 Hermann–Mauguin symbols; enter a, b, c, α, β, γ; real-time compatibility validation |

For file and code methods, **Apply** is disabled until a successful parse. For manual entry, **Apply** is always enabled. On accept, the result is written to the crystal's DB settings via `patch_crystal_settings()`.

When a PDB is fetched by code, the full PDB file is saved to `{proc_path}/files/{code}.pdb` if the crystal has a processing path set. This preserves the reference structure used for the experiment.

---

## 9. Processing Tab

### 9.1 Energy Definition

**Range mode** (default): start / end / increment. All values stored as integers.

**List mode**: comma- or space-separated integer values.

**Auto-detect** (checkbox, default ON):
- Scans the crystal's data path for files matching `<energy>_E<counter>_1_#####.cbf`
- Extracts, de-duplicates, and sorts energy values
- Forces list mode; disables manual editing
- Triggered on crystal load and by a `QFileSystemWatcher` on the data directory (debounced 300 ms)

### 9.2 Wedge Definition

| Parameter            | Default |
|----------------------|---------|
| Wedge size (images)  | 300     |
| Total images per sweep | 3600  |

**Auto-detect** (checkbox, default ON):
- Scans the primary sweep of the first detected energy for mtime gaps between consecutive frames
- A gap >60 s between consecutive frames marks a wedge boundary; a second matching gap is required for confirmation (guards against one-off network delays)
- Also counts total frames in the primary sweep to populate *Total images per sweep*
- Disables manual editing of both spinboxes while active
- Runs in a background thread to avoid blocking the GUI on slow NFS mounts
- Triggered on crystal load and by the same `QFileSystemWatcher` debounce as energy auto-detect

The derived wedge list is previewed in the UI.

### 9.3 Processing Pipeline

| Option      | SLURM script template        |
|-------------|------------------------------|
| AutoProc    | `autoproc_jobs.sh`           |
| Xia2 DIALS  | `xia2_dials_jobs.sh`         |
| Xia2 3dii   | `xia2_3dii_jobs.sh`          |

### 9.4 Submission Method

| Option                          | Behaviour                                                                         |
|---------------------------------|-----------------------------------------------------------------------------------|
| REST API (recommended)          | SSH to `wilson`, obtain SLURM JWT, POST each job to DLS SLURM REST API           |
| Dry run — generate scripts only | Write scripts to disk; log instructions for manual submission on Wilson           |

**Dry run** generates the driver and selected pipeline script, then logs the following instructions to the log panel:

```
Dry run — scripts generated, nothing submitted.
Scripts are in: {proc_path}/scripts

To submit manually, open a terminal on Wilson and run:
  cd {proc_path}
  bash scripts/run_spread_submit.sh

This will submit N job(s) via sbatch.
```

### 9.5 SSH Key Status

The status label shows whether passwordless SSH to `wilson` is configured, using `BatchMode=yes` with a 5-second timeout. The exact SSH error is shown as a tooltip when auth fails.

**Setup SSH key…** prompts for the DLS password and runs `ssh-copy-id` via `SSH_ASKPASS` so the password is never exposed on the terminal. The temporary askpass script is zero-overwritten and deleted immediately after use.

All SSH calls use `-o StrictHostKeyChecking=accept-new` to silently accept host keys on first connection.

---

## 10. Script Generation

### 10.1 Generated Files

Only the driver script and the **selected pipeline's** job script are written. Files are placed under the crystal's processing path:

```
{proc_path}/
  scripts/
    run_spread_submit.sh       Driver: iterates energies × wedges, calls pipeline script
    autoproc_jobs.sh      \
    xia2_dials_jobs.sh     |  One of these, depending on the selected pipeline
    xia2_3dii_jobs.sh     /
    phenix_dials_jobs.sh  \
    phenix_3dii_jobs.sh    |  Phenix refinement scripts (SPREAD tab)
    phenix_autoproc_jobs.sh/
  files/                       Reference files: PDBs from RCSB, anomalous groups .def
  {energy}eV/
    {images}img/
      xia2-dials/              Processing pipeline output
      xia2-3dii/
      autoPROC/
      phenix_dials_{run}/      phenix.refine output for this grid point
      phenix_3dii_{run}/
      phenix_autoproc_{run}/
  results/
    xia2-dials/                Processing analysis HTML report
    xia2-3dii/
    autoPROC/
    phenix_dials_{run}/        SPREAD analysis HTML report
    phenix_3dii_{run}/
    phenix_autoproc_{run}/
```

All generated scripts are marked executable.

### 10.2 Multi-sweep Support

When a data directory contains multiple sweeps for the same energy (e.g. `7118_E1_1_00001.cbf` and `7118_2_E1_1_00001.cbf`), the job scripts handle them automatically:

- The driver script (`run_spread_submit.sh`) receives a **cumulative** image count, not a per-sweep count. Directory names reflect cumulative totals: `300img`, `3600img`, `3900img` (sweep 1 full + 300 from sweep 2), `7200img` (both sweeps full), etc.
- At SLURM runtime each job script globs for all sweep files for that energy/counter, then derives from the cumulative count how many sweeps are fully included and how many images are needed from the current sweep.
- All sweeps up to and including the current one are passed as separate `image=` arguments (xia2) or `-Id` arguments (AutoProc), so the pipeline scales them together automatically without a separate scaling step.
- The energy auto-detection regex anchors to the start of the filename and requires ≥4 digits, preventing sweep-number suffixes (e.g. `_2_`) from being mistaken for energies.

### 10.3 Validation Before Generation

| Check                      | Error raised if…                          |
|----------------------------|-------------------------------------------|
| Energy list                | No valid energies defined                 |
| Wedge list                 | No valid wedges defined                   |
| Data path                  | Empty                                     |
| Processing path            | Empty                                     |

---

## 11. Job Submission

### 11.1 Overwrite Check

Before generating scripts, the GUI checks whether any pipeline output directories already exist matching `{proc_path}/*eV/*img/{pipeline_output_dir}/` (e.g. `xia2-dials`, `autoPROC`). If found, a confirmation dialog reports the number of existing directories and asks whether to delete them and re-submit. On confirmation:

- All matching output directories are removed via `shutil.rmtree`.
- The corresponding job records for that crystal and pipeline are deleted from the database.
- The tab indicator is reset.

If any deletion fails the process is aborted and an error is shown.

### 11.2 Script Generation

Scripts are generated for the selected pipeline only (see Section 10).

### 11.3 REST API Submission

1. SSH to `wilson` (interactive password prompt allowed) to retrieve a short-lived SLURM JWT token via `scontrol token lifespan=300`.
2. The GUI scans the data directory to discover all sweeps per energy. For each energy × sweep × wedge combination a job is built (cumulative image count).
3. For each job, a minimal wrapper script is POSTed to the DLS SLURM REST endpoint (`https://slurm-rest.diamond.ac.uk:8443/slurm/v0.0.40/job/submit`).
4. Successful submissions are recorded in the `jobs` table (including the SLURM job ID parsed from the REST response).
5. Progress is reported in the status bar; each result is logged.
6. After all submissions the `jobs_status_changed` signal is emitted and the tab indicator is updated.

If `requests` is not available, `urllib` is used as a fallback.

### 11.4 Dry Run

Scripts are generated and the user is shown instructions for manual submission on Wilson (see Section 9.4). No network calls are made and no jobs are recorded in the database.

### 11.5 Tab Indicator

A coloured dot is shown on the Processing tab label to reflect submission state for the currently loaded crystal:

| Dot colour | Meaning                                                              |
|------------|----------------------------------------------------------------------|
| *(none)*   | No real (non-dry-run) jobs recorded for this crystal                 |
| Yellow     | ≥1 job submitted; not all pipeline output directories exist yet      |
| Green      | All recorded job output directories are present on the filesystem    |

The indicator is evaluated on startup, on crystal load, after each submission, and every 60 seconds by a background filesystem poll.

### 11.6 Job Monitoring (Filesystem Polling)

A `QTimer` fires every 60 seconds. For each job with `status = 'submitted'`, the GUI checks whether `{proc_dir}/{output_dir}` exists on the filesystem. If so, the job status is updated to `completed` in the database and the tab indicator is refreshed.

This provides passive completion detection without requiring SLURM API access. Full SLURM state polling (PENDING / RUNNING / COMPLETED / FAILED via the REST API) is planned once DLS REST authentication is resolved.

---

## 12. Analyse Processing

The "Analyse Processing" section is embedded at the bottom of the **Processing** tab, below the submission controls.

### 12.1 Input

- **Processing path**: directory containing `{energy}eV` subdirectories (as written by the processing scripts). Automatically kept in sync with the processing path field in the Processing tab.
- **Pipeline**: xia2-dials, xia2-3dii, or autoPROC.

### 12.2 xia2 Output Parsing

For each `{energy}eV/{images}img/{pipeline}/xia2.txt` file found:

- The statistics table is located and parsed.
- Values extracted for overall, low-resolution shell, and high-resolution shell: high-resolution limit, completeness, multiplicity, I/σ(I), Rmerge(I+/I−), Rpim, CC½, anomalous completeness, anomalous multiplicity, anomalous slope, Wilson B-factor.
- Unit cell parameters (a, b, c, α, β, γ) parsed from the header; standard-uncertainty suffixes (e.g. `89.2764(4)`) stripped before parsing.

A run with no statistics table (failed or incomplete xia2) is recorded as absent and shown as a red cross (✗) in the report.

### 12.3 autoPROC Output Parsing

For each `{energy}eV/{images}img/aP.log` file found:

- ANSI escape codes are stripped.
- The **third and final statistics block** is located — identified by the phrase *"statistics below are for all observations up to the maximum resolution as determined by STARANISO"*. This block uses the ellipsoidal STARANISO cut-off and is the recommended output.
- Values extracted for overall, inner shell, and outer shell: high-resolution limit, completeness (spherical and ellipsoidal), multiplicity, I/σ(I), Rmerge/Rmeas/Rpim (all I+&I−), CC½, anomalous completeness (spherical and ellipsoidal), anomalous multiplicity, CC(ano), |DANO|/σ(DANO).
- The **diffraction limits along the three principal axes of the STARANISO ellipsoid** are extracted from the `Diffraction limits & principal axes` section. The three values are labelled a*, b*, c* by position (first, second, third line) regardless of the actual reciprocal-space direction.
- Cell parameters, space group, and wavelength are extracted from the last occurrence of the corresponding output lines.

A run where the STARANISO block is absent (failed or still-running job) is recorded as `None` and shown as ✗ in the report.

### 12.4 Report Generation

Output is written to `{processing_path}/results/{pipeline}/` (default) or a user-chosen directory.

If the default output directory already exists, a dialog offers three options:

| Option                  | Behaviour                                                  |
|-------------------------|------------------------------------------------------------|
| Show existing results   | Opens `index.html` in the default browser; no re-run       |
| Re-run and overwrite    | Confirms, then re-runs into the same directory             |
| Re-run in new directory | Prompts for a subdirectory name under `results/`           |

**Generated artefacts**: PNG plots plus `index.html`.

The HTML report contains:
- A colour-coded status table (green ✓ / red ✗ per energy/wedge combination). Each cell links to the pipeline's own HTML summary (`xia2.html` or `autoPROC/summary.html`) when the file exists, opening in a new browser tab.
- Navigation links to each plot.
- Embedded plots as `<img>` tags.

All plots show wedge size on the x-axis. Multi-shell metrics (overall / low / high) are shown as three-panel figures.

**Plots generated for xia2** (11 total):

| Plot                    | Metric                                    |
|-------------------------|-------------------------------------------|
| High resolution limit   | Overall                                   |
| Completeness            | Overall                                   |
| Multiplicity            | Overall                                   |
| I/σ(I)                  | Overall / Low / High shell                |
| Rmerge(I+/I−)           | Overall / Low / High shell                |
| CC½                     | Overall / Low / High shell                |
| Wilson B factor         | Scalar                                    |
| Anomalous completeness  | Overall / Low / High shell                |
| Anomalous multiplicity  | Overall / Low / High shell                |
| Anomalous slope         | Scalar                                    |
| Unit cell parameters    | 2×3 grid: a, b, c, α, β, γ              |

**Plots generated for autoPROC** (10 total, using ellipsoidal statistics throughout):

| Plot                              | Metric                                    |
|-----------------------------------|-------------------------------------------|
| High resolution limit (STARANISO) | Overall                                   |
| Completeness (ellipsoidal)        | Overall                                   |
| Multiplicity                      | Overall                                   |
| I/σ(I)                            | Overall / Low / High shell                |
| Rmerge (all I+&I−)                | Overall / Low / High shell                |
| CC½                               | Overall / Low / High shell                |
| Anomalous completeness (ellipsoidal) | Overall / Low / High shell             |
| Anomalous multiplicity            | Overall / Low / High shell                |
| Unit cell parameters              | 2×3 grid: a, b, c, α, β, γ              |
| **Diffraction limits (STARANISO)**| 3 panels: a*, b*, c* principal axes      |

Report generation runs in a `QThread` to keep the GUI responsive. Progress messages are appended to the log panel.

---

## 13. SPREAD Tab – Phenix Anomalous Refinement

### 13.1 Overview

The **SPREAD** tab submits `phenix.refine` anomalous refinement jobs across the full energy/wedge grid and analyses the resulting f′/f″ values as a function of energy. It operates on the output of any of the three processing pipelines (xia2-dials, xia2-3dii, autoPROC).

### 13.2 Submit Jobs

#### Input

| Field                | Description                                                                                 |
|----------------------|---------------------------------------------------------------------------------------------|
| Processing path      | Inherited from the loaded crystal; shown read-only                                          |
| Pipeline             | Radio buttons: xia2-dials, xia2-3dii, autoPROC. Greyed out when no MTZ found for that pipeline |
| PDB model            | Browse to a `.pdb` or `.cif` file; copied immediately to `{proc_path}/files/`              |
| Anomalous groups     | Browse to a `.def` file; copied immediately to `{proc_path}/files/`                        |
| Macro cycles         | Integer spinbox, default 6                                                                  |

File dialogs open at `{proc_path}/files/` by default. If the selected file already lives there, no copy is made.

#### Job status

The detected run number (next available, based on existing `phenix_{pipeline}_{N}` directories) and the number of jobs ready (one per MTZ found) are displayed. A **Refresh** button re-scans the filesystem.

#### Pipeline-specific behaviour

Each pipeline uses a distinct output directory prefix and job script:

| Pipeline    | Output dir prefix  | Script name              | MTZ location                                    |
|-------------|--------------------|--------------------------|-------------------------------------------------|
| xia2-dials  | `phenix_dials`     | `phenix_dials_jobs.sh`   | `xia2-dials/DataFiles/*_free.mtz` (glob)        |
| xia2-3dii   | `phenix_3dii`      | `phenix_3dii_jobs.sh`    | `xia2-3dii/DataFiles/*_free.mtz` (glob)         |
| autoPROC    | `phenix_autoproc`  | `phenix_autoproc_jobs.sh`| `autoPROC/staraniso_alldata-unique.mtz` (fixed) |

For xia2, the MTZ filename encodes the project and crystal name and is not known at submission time. The job script resolves it at runtime via a bash glob (`ls .../DataFiles/*_free.mtz | head -1`), aborting the job if none is found.

The `miller_array.labels.name` arguments passed to `phenix.refine` differ by pipeline:

| Pipeline    | Data labels                                  | Free-R label    |
|-------------|----------------------------------------------|-----------------|
| xia2        | `I(+),SIGI(+),I(-),SIGI(-)`                 | `FreeR_flag`    |
| autoPROC    | `I(+),SIGI(+),I(-),SIGI(-),merged`          | `FreeR_flag`    |

Each job script uses `#SBATCH --cpus-per-task=16`.

#### Submission method

Identical to the Processing tab: REST API (recommended) or dry run. Dry run writes the script and logs manual `sbatch` instructions.

Before each submission run, the existing `phenix_{pipeline}_{run}` output directories are removed and recreated by the job script itself (not by the GUI).

### 13.3 Analyse SPREAD

#### Input

Three rows of controls, one per pipeline, each with a radio button and a run-number spinbox:

- Radio buttons are greyed out when no `phenix_{prefix}_N` directories exist for that pipeline.
- Spinboxes are auto-populated with the **last existing run number** when a crystal is loaded or the processing path changes.
- The selected radio button and spinbox value determine which results are parsed.

#### Output directory

`{proc_path}/results/{pipeline_prefix}_{run}/phenix_analysis.html`

The label below the input group shows the resolved path before the analysis is run.

#### Parsing (`phenix_parser`)

For each log file matching `{proc_path}/*eV/*img/{prefix}_{run}/*_refine_001.log`:

- Energy and images are extracted from the directory path (`{E}eV/{N}img`).
- The final R-work and R-free are extracted from the `Final R-work = …, R-free = …` line.
- All `Anomalous scatterer group:` blocks are collected (one per macro cycle plus the initial zeros-block). The number of unique group selections determines `n_groups`; the last `n_groups` blocks contain the final macro-cycle values.
- Results with no anomalous groups are excluded.

#### Report generation (`phenix_analysis`)

The HTML report is self-contained (all plots embedded as base64 PNG) and contains:

1. **Overview — f′ (all wedges)**: one subplot per anomalous group, one line per wedge size, x-axis = energy.
2. **Overview — f″ (all wedges)**: same structure for f″.
3. **Per-wedge sections** (ordered by increasing wedge size): for each wedge, f′ and f″ vs energy side-by-side with one line per group, followed by a table of R-work, R-free, and f′/f″ per energy.

The anomalous group label used in plots is derived from the selection string as `{chain}-{resid}-{atom}` (e.g. `A-401-CA`).

Report generation runs in a `QThread`. On completion the report is opened automatically in the default browser.

---

## 14. Settings Persistence

- Settings are auto-saved 1 second after any field change (debounced `QTimer`).
- On save: the INI file is updated and, if a crystal is loaded, `update_crystal()` is called to persist the full form state to the DB.
- On startup: settings are loaded from INI; the last-loaded crystal ID is restored; the header labels are populated; the energy auto-scan is triggered.
- On crystal load: DB settings override INI for all form fields.
- On application close: a confirmation dialog offers Save + Quit, Quit without saving, or Cancel.

---

## 15. Platform and Deployment

- Designed for Diamond Linux workstations (RHEL-based).
- Supports remote NX and X11 forwarding (`ssh -Y`).
- `QT_QPA_PLATFORM=xcb` is forced to avoid Wayland/OpenGL initialisation issues.
- No installer required; run via `uv run spread-gui` from the repository root.
- Python 3.10+ required (tested on 3.10 and 3.11).

---

## 16. Limitations

- Primary filename pattern: `<energy>_E<counter>_1_#####.cbf`; additional sweeps must follow the `<energy>_<N>_E<counter>_1_#####.cbf` convention.
- Space group and unit cell must be set via Manage Projects before script generation.
- No batch submission across multiple crystals in a single operation.
- Wedge auto-detection requires that CBF files are accessible on a locally mounted filesystem; detection is skipped silently if the data path is unavailable.

---

## 17. Change History

| Date       | Change                                                                                   |
|------------|------------------------------------------------------------------------------------------|
| May 2026   | SPREAD tab: phenix.refine anomalous refinement submission across the energy/wedge grid (per-pipeline scripts, auto-detected run number, MTZ discovery, pipeline-specific miller labels). Analyse SPREAD section: phenix.refine log parsing (`phenix_parser`), f′/f″ vs energy HTML report with overview and per-wedge plots (`phenix_analysis`). Analysis tab embedded in Processing tab as "Analyse Processing". Shared log panel (permanent, QSplitter). Application restructured to two top-level tabs (Processing, SPREAD). |
| April 2026 | Job tracking: `jobs` table in DB; tab indicator (yellow/green dot); 60 s filesystem polling for completion detection. autoPROC output parsing from `aP.log` STARANISO block; autoPROC analysis with diffraction-limits plot. Submission simplified: dry-run radio replaces checkbox and sbatch option; only selected pipeline script generated. Run-status table cells link to pipeline HTML summaries. Overwrite deletes existing output dirs and clears DB records before re-submitting. |
| April 2026 | Multi-sweep support: cumulative wedge directories, per-sweep image ranges, automatic sweep discovery at runtime; wedge auto-detection from CBF timestamps (background thread); scripts and files moved to `scripts/` and `files/` subdirectories; PDB saving restored |
| April 2026 | Full rewrite: project/crystal DB, Manage Projects dialog, SgCellDialog, Analysis tab, header bar, REST API submission, removal of path fields from Processing tab |
| March 2026 | Initial specification aligned with DLS software documentation conventions                |
