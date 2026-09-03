from __future__ import annotations

import getpass
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import __version__
from .project_store import FACILITIES, ProjectCatalog, ProjectDatabase, ProjectError
from .settings import Settings


def git_commit(start: Path | None = None) -> str:
    override = os.environ.get("FLUMEWORKS_COMMIT")
    if override:
        return override
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=start or Path.cwd(),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


class ApplicationState:
    def __init__(self, settings: Settings, model_design_url: str, repository_root: Path | None = None):
        self.settings = settings
        self.model_design_url = model_design_url
        self.catalog = ProjectCatalog(settings.project_root)
        self.commit = git_commit(repository_root)
        self._current: ProjectDatabase | None = None
        self._lock = threading.RLock()

    def _record_run(self, database: ProjectDatabase) -> None:
        database.record_application_run(
            app_version=__version__,
            git_commit=self.commit,
            computer_name=os.environ.get("COMPUTERNAME", "unknown"),
            user_name=getpass.getuser(),
        )

    def create_project(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            database = self.catalog.create(**values)
            self._current = database
            self._record_run(database)
            return self.current_payload()

    def open_project(self, project_uuid: str) -> dict[str, Any]:
        with self._lock:
            database = self.catalog.open_uuid(project_uuid)
            self._current = database
            self._record_run(database)
            return self.current_payload()

    def add_design_condition(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            if self._current is None:
                raise ProjectError("Create or open a project before adding design conditions.")
            return self._current.add_design_condition(**values)

    def current_payload(self) -> dict[str, Any] | None:
        with self._lock:
            if self._current is None:
                return None
            return {
                "project": self._current.project_dict(),
                "designConditions": self._current.design_conditions(),
            }

    def bootstrap_payload(self) -> dict[str, Any]:
        return {
            "application": {
                "name": "WRL FlumeWorks",
                "version": __version__,
                "gitCommit": self.commit,
            },
            "projectRoot": str(self.settings.project_root),
            "facilities": [{"id": key, "name": name} for key, name in FACILITIES.items()],
            "projects": self.catalog.list(),
            "currentProject": self.current_payload(),
            "modelDesignUrl": self.model_design_url,
        }

