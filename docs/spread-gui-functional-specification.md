# SPREAD GUI – Functional Specification

| Field          | Value                                         |
|----------------|-----------------------------------------------|
| Document owner | Armin Wagner                                  |
| Application    | SPREAD Processing Pipeline GUI                |
| Status         | Current implementation                        |
| Last updated   | April 2026                                    |
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
- xia2 output analysis and report generation
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
- Generate SLURM-compatible job scripts
- Optionally submit jobs to the Diamond compute cluster via REST API or `sbatch`
- Analyse xia2 output across energy points and generate an HTML report with plots

The application is designed for interactive use on Diamond Linux workstations and via remote NX sessions.

---

## 5. Software Architecture

### 5.1 Entry Point

`spread_gui.app.main()` initialises `QApplication` and instantiates `MainWindow`.

### 5.2 Major Components

| Component              | Responsibility                                                  |
|------------------------|-----------------------------------------------------------------|
| `MainWindow`           | Application shell, header bar, tab container, status bar        |
| `ProcessingTab`        | Energy/wedge/pipeline configuration, script generation, submission |
| `AnalysisTab`          | xia2 result parsing and HTML report generation                  |
| `ManageProjectsDialog` | Two-pane project/crystal browser; crystal creation and deletion |
| `SgCellDialog`         | Space group and unit cell definition (three input methods)      |
| `ProjectDB`            | SQLite-backed persistence for projects, crystals, and settings  |

### 5.3 Layout

- `ProcessingTab` is embedded in a `QScrollArea` for usability on small displays.
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
```

- Crystals are deleted automatically when their parent project is deleted (`ON DELETE CASCADE`).
- The `settings` JSON uses the same key names as `settings.ini` so either source can populate the form without conversion.

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
| Total images         | 3600    |

The derived wedge list is previewed in the UI.

### 9.3 Processing Pipeline

| Option      | SLURM script template        |
|-------------|------------------------------|
| AutoProc    | `autoproc_jobs.sh`           |
| Xia2 DIALS  | `xia2_dials_jobs.sh`         |
| Xia2 3dii   | `xia2_3dii_jobs.sh`          |

### 9.4 Submission Method

| Option                          | Behaviour                                           |
|---------------------------------|-----------------------------------------------------|
| REST API (recommended)          | SSH to `wilson`, obtain SLURM JWT, POST to DLS REST API |
| sbatch (fallback)               | Run `sbatch` locally (requires Wilson shell access) |

**Dry run** checkbox (default ON) — logs the commands that would be submitted without actually submitting.

### 9.5 SSH Key Status

The status label shows whether passwordless SSH to `wilson` is configured, using `BatchMode=yes` with a 5-second timeout. The exact SSH error is shown as a tooltip when auth fails.

**Setup SSH key…** prompts for the DLS password and runs `ssh-copy-id` via `SSH_ASKPASS` so the password is never exposed on the terminal. The temporary askpass script is zero-overwritten and deleted immediately after use.

All SSH calls use `-o StrictHostKeyChecking=accept-new` to silently accept host keys on first connection.

---

## 10. Script Generation

### 10.1 Generated Files

All files are written to the crystal's processing path.

| File                    | Purpose                                  |
|-------------------------|------------------------------------------|
| `run_spread_submit.sh`  | Driver: iterates over energies and wedges, calls the pipeline script via `sbatch` |
| `autoproc_jobs.sh`      | AutoProc SLURM job template              |
| `xia2_dials_jobs.sh`    | Xia2 DIALS SLURM job template           |
| `xia2_3dii_jobs.sh`     | Xia2 3dii SLURM job template            |

All generated scripts are marked executable.

### 10.2 Validation Before Generation

| Check                      | Error raised if…                          |
|----------------------------|-------------------------------------------|
| Energy list                | No valid energies defined                 |
| Wedge list                 | No valid wedges defined                   |
| Data path                  | Empty                                     |
| Processing path            | Empty                                     |

---

## 11. Job Submission

1. Scripts are generated (as above).
2. For REST API mode: SSH to `wilson` (interactive password prompt allowed) to retrieve a short-lived SLURM JWT token via `scontrol token`.
3. For each energy × wedge combination, a minimal wrapper script is POSTed to the DLS SLURM REST endpoint (`https://slurm-rest.diamond.ac.uk:8443/slurm/v0.0.40/job/submit`).
4. Progress is reported in the status bar: `submitted / total`.
5. Each submission result is logged.

If `requests` is not available, `urllib` is used as a fallback.

---

## 12. Analysis Tab

### 12.1 Input

- **Processing path**: directory containing `{energy}eV` subdirectories (as written by the processing scripts).
- **Pipeline**: xia2-dials or xia2-3dii.

The processing path is pre-populated from the shared `settings.ini`.

### 12.2 xia2 Output Parsing

For each `{energy}eV/{images}img/{pipeline}/xia2.txt` file found:

- The statistics table is located and parsed.
- Values are extracted for overall, low-resolution shell, and high-resolution shell.
- Fields extracted: high-resolution limit, completeness, multiplicity, I/σ(I), R-merge, R-pim, CC½, anomalous completeness, anomalous multiplicity, anomalous slope, Wilson B-factor.
- Unit cell parameters (a, b, c, α, β, γ) are parsed from the header (standard-uncertainty notation stripped before parsing).

A run with no statistics table (failed xia2) is recorded as absent and shown as a red cross (✗) in the report.

### 12.3 Report Generation

Output is written to `{processing_path}/results/{pipeline}/` (default) or a user-chosen directory.

If the default output directory already exists, a dialog offers three options:

| Option              | Behaviour                                                  |
|---------------------|------------------------------------------------------------|
| Show existing results | Opens `index.html` in the default browser; no re-run    |
| Re-run and overwrite | Confirms, then re-runs into the same directory           |
| Re-run in new directory | Prompts for a subdirectory name under `results/`      |

**Generated artefacts**: one PNG per metric (11 total), plus `index.html`.

The HTML report contains:
- A colour-coded status table (green ✓ / red ✗ per energy/wedge combination)
- Navigation links to each plot
- Embedded plots as `<img>` tags

All plots show wedge size on the x-axis. Multi-shell metrics (overall / low / high) are shown as three-panel figures.

Report generation runs in a `QThread` to keep the GUI responsive. Progress messages are emitted as signals and appended to the log panel.

---

## 13. Settings Persistence

- Settings are auto-saved 1 second after any field change (debounced `QTimer`).
- On save: the INI file is updated and, if a crystal is loaded, `update_crystal()` is called to persist the full form state to the DB.
- On startup: settings are loaded from INI; the last-loaded crystal ID is restored; the header labels are populated; the energy auto-scan is triggered.
- On crystal load: DB settings override INI for all form fields.
- On application close: a confirmation dialog offers Save + Quit, Quit without saving, or Cancel.

---

## 14. Platform and Deployment

- Designed for Diamond Linux workstations (RHEL-based).
- Supports remote NX and X11 forwarding (`ssh -Y`).
- `QT_QPA_PLATFORM=xcb` is forced to avoid Wayland/OpenGL initialisation issues.
- No installer required; run via `uv run spread-gui` from the repository root.
- Python 3.10+ required (tested on 3.10 and 3.11).

---

## 15. Limitations

- Single filename pattern supported for raw data (`<energy>_E<counter>_1_#####.cbf`).
- Space group and unit cell must be set via Manage Projects before script generation.
- No batch submission across multiple crystals in a single operation.

---

## 16. Change History

| Date       | Change                                                                                   |
|------------|------------------------------------------------------------------------------------------|
| April 2026 | Full rewrite: project/crystal DB, Manage Projects dialog, SgCellDialog, Analysis tab, header bar, REST API submission, removal of path fields from Processing tab |
| March 2026 | Initial specification aligned with DLS software documentation conventions                |
