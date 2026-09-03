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

Each physical-model project is a directory containing `project.flumeworks`, a SQLite database.
SQLite was selected over one large CSV because the planned system has related data sets: projects,
design conditions, DAQ files, channels, calibrations, processing runs, results, and audit history.
CSV remains an import/export format for engineers and existing instruments.

Schema changes are versioned. Application runs record the FlumeWorks version and Git commit in the
active project database, providing the start of the requested processing audit trail.

## Model Design compatibility boundary

The existing HTML viewer and Python model helper are kept in `src/flumeworks/model_design`. They run
on their own loopback port and are displayed by the shell in an iframe. This deliberately isolates
the proven tool while the surrounding program evolves. See `src/flumeworks/model_design/UPSTREAM.md`
for its exact provenance and integrity hashes.

Model Design project JSON remains its current portable format in this milestone. Linking its state
to the new project database is a later migration step and should be implemented through an adapter,
not by silently changing the imported viewer.

