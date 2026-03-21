
SPREAD GUI – Functional Specification (DLS Software Documentation Aligned)
Document Control

Field	Value
Document owner	Armin Wagner
Application	SPREAD Processing Pipeline GUI
Repository	Privat
Status	Current implementation
Last updated	March 2026
Audience	Beamline scientists, scientific software developers, maintainers



1. Purpose
This document describes the current implemented functionality of the SPREAD Processing Pipeline GUI. It is intended to support:

Operational use on the I23 beamline
Software maintenance and handover
Review during technical discussions and internal audits
This document follows the Diamond Light Source software documentation conventions, focusing on behaviour, interfaces, and operational robustness rather than future design proposals.


2. Scope
In scope

User-visible GUI behaviour
Processing logic and validation rules
Script generation and job submission
Platform and operational constraints
Out of scope

Algorithmic details of SPREAD itself
Analysis or post-processing workflows
Future or proposed features not yet implemented


3. Intended Users

Beamline scientists and users configuring and submitting SPREAD processing and analysis
Scientific software developers maintaining or extending the GUI
Support staff diagnosing operational issues
Users are assumed to be familiar with:

Diamond visit structure
SLURM job submission
Crystallographic terminology (space group, unit cell, energies, wedges)


4. System Overview
The SPREAD GUI is a PyQt6 desktop application providing a structured interface to:

Collect and validate processing parameters
Automatically derive energies and metadata from collected data
Generate SLURM-compatible job scripts
Optionally submit jobs to the Diamond compute cluster
The application is designed for interactive use on Diamond workstations and via remote NX sessions.


5. Software Architecture
5.1 Entry Point

main() initialises QApplication
MainWindow is instantiated as the top-level container

5.2 Major Components

Component	Responsibility
MainWindow	Application shell, menu, status bar
ProcessingTab	Primary functional interface
AnalysisTab	Placeholder (non-functional)

5.3 Layout and Usability

ProcessingTab is embedded in a QScrollArea
Ensures usability on laptops and small displays
No hard-coded window size assumptions


6. Operational Workflow

User launches GUI within a Diamond visit context
Visit, data, and processing paths are auto-detected
PDB and crystallographic metadata are loaded and validated
Energies and wedges are defined (manually or automatically)
Processing pipeline is selected
Job scripts are generated
Jobs are optionally submitted via SLURM


7. Functional Specification
7.1 Visit, Project, and Crystal Identification
Behaviour:

Visit ID auto-detected from current working directory
Validation against pattern: `^[a-z]{2}[0-9]{5}-[0-9]{1,3}
Manual override always permitted
Derived paths:

Data path: visit root
Processing path: <visit>/processing/SPREAD


7.2 PDB Input and Metadata Extraction
Supported modes:

Local PDB file upload
Fetch by 4-character PDB code (RCSB)Implemented behaviour:
Parse CRYST1 record
Extract unit cell and space group
Attempt automatic space-group matching
GUI indicates provenance of values


7.3 Space Group and Unit Cell Validation
Implementation:

Space group list populated with all 230 Hermann–Mauguin symbols
Generated dynamically using gemmi when availableValidation rules:
Unit cell parameters are editable
Compatibility checks performed in real time
Visual feedback:Green: compatible
Red: incompatible, with explanation


7.4 Energy Definition
7.4.1 Manual Definition

Range mode: start / end / increment
List mode: comma- or space-separated values
All energies are internally stored as integers
7.4.2 Automatic Energy Detection
Source:

Filenames in the selected Data path
Expected filename pattern:
<energy>_E<counter>_1_#####.cbf



Behaviour:

Regex-based extraction
Conversion to integer values
De-duplication and sorting
Dynamic updates:

Triggered on Data path edits (debounced)
Triggered on directory content changes (QFileSystemWatcher)
User control:

Checkbox: Auto-detect energies from Data path (default: ON)

When enabled:Energy mode forced to List
Manual editing disabled


7.5 Wedge Definition
Parameters:

Wedge size (images per wedge)
Total number of images
Defaults:

Wedge size: 30
Total images: 360
Derived output:

Wedge list generated automatically
Preview displayed in GUI


7.6 Processing Pipeline Selection
Supported pipelines:

AutoProc
Xia2 DIALS
Xia2 3dii
Pipeline selection determines the job script template used.


7.7 Script Generation
Generated artefacts:

File	Purpose
run_spread_submit.sh	Driver script
autoproc_jobs.sh	AutoProc jobs
xia2_dials_jobs.sh	Xia2 DIALS jobs
xia2_3dii_jobs.sh	Xia2 3dii jobs

Characteristics:

Energies passed as integers
Scripts marked executable
Filename templates aligned with detected data


7.8 Job Submission

Submission via sbatch is optional
Dry-run mode enabled by default
All commands logged
Progress reporting:

Total jobs = len(energies) × len(wedges)
Status bar displays percentage completion


8. Logging and Diagnostics

Central log panel records all major actions
Includes:Script generation
Dry-run commands
Submission output
Validation warnings
Logging is intended for user transparency and first-line support diagnostics.


9. Platform and Deployment Considerations

Designed for Diamond Linux workstations
Supports remote X11 (ssh -Y)
Safeguards include:Forcing QT_QPA_PLATFORM=xcb
Disabling problematic OpenGL initialisation
No installer is required; application is run from the managed environment.


10. Limitations

Single filename pattern supported
No persistent user settings
Analysis tab not implemented
No batch handling of multiple crystals


11. Support and Maintenance Notes

Issues should be reproducible using dry-run mode
Logs should be attached to support requests
Code changes should preserve integer-energy handling


12. Change History

Date	Change
Mar 2026	Aligned with DLS software documentation conventions

