# Architecture

## Application shell

FlumeWorks is a Python application with a local FastAPI interface displayed in a pywebview desktop
window. This keeps the user experience close to the existing browser tool while allowing later DAQ,
signal-processing, reporting, video, and model integrations to run in Python.

The browser interface contains top-level workspaces. The first two are:

- **Project** — project metadata, facility selection, and design wave conditions.
- **Model Design** — the existing wave-flume CAD and bathymetry viewer.

Future processing modules should be added as separate Python packages and top-level workspaces,
without growing Model Design into a monolith.

## Project data

Each physical-model project is a single portable `.flumeworks` SQLite database at a location chosen
by the user. SQLite was selected over one large CSV because the planned system has related data
sets: projects, design conditions, Model Design state, DAQ files, channels, calibrations, processing
runs, results, and audit history. CSV remains an import/export format for engineers and existing
instruments; large time-series files remain external and are referenced by the project.

For responsive network-drive use, the application takes an atomic sidecar lease and copies the
database to a per-computer working cache. Save and close use SQLite's online-backup mechanism to
produce a verified snapshot at the selected project path. An adjacent `flumeworks_backups` folder
contains user-requested timestamped snapshots. Recent paths are machine-local preferences, not
project data.

Schema changes are versioned. Application runs record the FlumeWorks version and Git commit in the
active project database, providing the start of the requested processing audit trail.

## Model Design compatibility boundary

The existing HTML viewer and Python model helper are kept in `src/flumeworks/model_design`. They run
on their own loopback port and are displayed by the shell in an iframe. This deliberately isolates
the proven tool while the surrounding program evolves. See `src/flumeworks/model_design/UPSTREAM.md`
for its exact provenance and integrity hashes.

The shell and viewer exchange versioned Model Design state through a narrow `postMessage` bridge.
The complete viewer state—including flume/CAD reference data, bathymetry, placement, wave
conditions, layer visibility, and model settings—is stored in the active `.flumeworks` database.
Model Design JSON remains available as a workspace-only import/export format and is deliberately
labelled separately from the complete project file.
