# Getting started

WRL FlumeWorks is currently a source checkout for the first two migration milestones:

1. a Python desktop shell with a project database; and
2. the existing wave-flume CAD and bathymetry viewer inside **Model Design**.

## Run on a WRL Windows computer

1. Install [uv](https://docs.astral.sh/uv/) once if it is not already installed.
2. Double-click `run_flumeworks.cmd` in the repository root.
3. Create a project from the **Project** workspace and choose where to save its `.flumeworks` file.
4. Open **Model Design** from the left navigation.

The first launch creates a local Python environment and may take a little longer. A project is one
portable `.flumeworks` file. It may be saved in any local or network folder; an existing file is
never silently overwritten. FlumeWorks remembers recently opened files on each computer.

While a project is open, FlumeWorks holds an adjacent `.lock` file so another user cannot edit the
same project simultaneously. Work is performed on a fast local copy and written back when **Save
project** is selected or the project is closed normally. Use **Generate backup** to create a dated
copy beside the project in `flumeworks_backups`.

The project file includes project metadata, design conditions, the selected flume and drawing
state, bathymetry, and wave conditions. The **Export model design** and **Import model design**
buttons in Model Design exchange only that workspace as JSON; they are useful for transferring a
design independently, but do not replace the complete `.flumeworks` project.

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
