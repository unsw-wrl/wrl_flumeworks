# Getting started

WRL FlumeWorks is currently a source checkout for the first two migration milestones:

1. a Python desktop shell with a project database; and
2. the existing wave-flume CAD and bathymetry viewer inside **Model Design**.

## Run on a WRL Windows computer

1. Install [uv](https://docs.astral.sh/uv/) once if it is not already installed.
2. Double-click `run_flumeworks.cmd` in the repository root.
3. Create a project from the **Project** workspace.
4. Open **Model Design** from the left navigation.

The first launch creates a local Python environment and may take a little longer. By default,
project folders are created under `Documents\WRL FlumeWorks Projects`. Each folder contains one
`project.flumeworks` SQLite database. Existing non-empty folders and existing databases are never
overwritten.

## Optional local model configuration

Copy `flumeworks_config.example.json` to `flumeworks_config.json` and set the installed SWAN,
SWASH, or XBeach executable paths. The local configuration file is deliberately ignored by Git.
The imported Model Design workspace can also be used without these model executables.

## Developer setup

```powershell
uv sync --extra dev
uv run pytest
uv run flumeworks
```

Use `uv run flumeworks --browser` to host the same interface in a normal browser while developing.

