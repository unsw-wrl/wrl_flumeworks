from __future__ import annotations

import getpass
import os
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .project_store import (
    BACKUP_FOLDER_NAME,
    FACILITIES,
    PROJECT_EXTENSION,
    ProjectDatabase,
    ProjectError,
    ProjectExistsError,
    ProjectLease,
    RecentProjects,
    local_working_path,
    normalise_project_path,
    project_slug,
    snapshot_database,
)
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
        self.recents = RecentProjects(settings.state_root)
        self.commit = git_commit(repository_root)
        self._current: ProjectDatabase | None = None
        self._source_path: Path | None = None
        self._lease: ProjectLease | None = None
        self._dirty = False
        self._lease_error = ""
        self._lock = threading.RLock()
        self._stop_heartbeat = threading.Event()
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name="flumeworks-project-lock",
            daemon=True,
        )
        self._heartbeat.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(30):
            with self._lock:
                if self._lease is None:
                    continue
                try:
                    self._lease.refresh()
                except (OSError, ProjectError) as exc:
                    self._lease_error = f"Project lock could not be refreshed: {exc}"

    def _record_run(self, database: ProjectDatabase) -> None:
        database.record_application_run(
            app_version=__version__,
            git_commit=self.commit,
            computer_name=os.environ.get("COMPUTERNAME", "unknown"),
            user_name=getpass.getuser(),
        )

    def _require_current(self) -> ProjectDatabase:
        if self._current is None:
            raise ProjectError("Create or open a project first.")
        return self._current

    def _require_editable(self) -> ProjectDatabase:
        database = self._require_current()
        if self._lease is None or not self._lease.acquired:
            raise ProjectError(self._lease_error or "The project edit lock is no longer available.")
        return database

    def _activate(self, database: ProjectDatabase, source_path: Path, lease: ProjectLease) -> dict[str, Any]:
        self._current = database
        self._source_path = source_path
        self._lease = lease
        self._lease_error = ""
        self._record_run(database)
        self._dirty = True
        self.recents.add(database, source_path)
        return self.current_payload()

    def create_project(self, *, destination_path: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            destination = normalise_project_path(destination_path)
            if destination.exists():
                raise ProjectExistsError(f"Project file already exists; nothing was overwritten: {destination}")
            if self._current is not None:
                self.close_current(save=True)
            lease = ProjectLease(destination, app_version=__version__)
            lease.acquire()
            working = local_working_path(self.settings.state_root, destination)
            temporary = working.with_name(f"new-{uuid.uuid4().hex}{PROJECT_EXTENSION}")
            try:
                temporary.parent.mkdir(parents=True, exist_ok=True)
                database = ProjectDatabase.create(temporary, **values)
                snapshot_database(database.path, working, replace=True)
                temporary.unlink(missing_ok=True)
                database = ProjectDatabase(working)
                snapshot_database(database.path, destination, replace=False)
                return self._activate(database, destination, lease)
            except Exception:
                temporary.unlink(missing_ok=True)
                lease.release()
                raise

    def open_project(self, source_path: str) -> dict[str, Any]:
        with self._lock:
            source = normalise_project_path(source_path)
            if not source.is_file():
                raise ProjectError(f"FlumeWorks project does not exist: {source}")
            if self._source_path == source and self._current is not None:
                return self.current_payload()
            if self._current is not None:
                self.close_current(save=True)
            lease = ProjectLease(source, app_version=__version__)
            lease.acquire()
            working = local_working_path(self.settings.state_root, source)
            try:
                snapshot_database(source, working, replace=True)
                database = ProjectDatabase(working)
                return self._activate(database, source, lease)
            except Exception:
                lease.release()
                raise

    def add_design_condition(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            condition = self._require_editable().add_design_condition(**values)
            self._dirty = True
            return condition

    def save_model_design(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            database = self._require_editable()
            if database.model_design_state() != payload:
                database.save_model_design_state(payload)
                self._dirty = True
            return self.current_payload()

    def save_current(self) -> dict[str, Any]:
        with self._lock:
            database = self._require_editable()
            if self._source_path is None:
                raise ProjectError("The current project has no source file.")
            if self._dirty:
                snapshot_database(database.path, self._source_path, replace=True)
                self._dirty = False
            if self._lease:
                self._lease.refresh()
            self.recents.add(database, self._source_path)
            return self.current_payload()

    def backup_current(self) -> Path:
        with self._lock:
            database = self._require_editable()
            if self._source_path is None:
                raise ProjectError("The current project has no source file.")
            folder = self._source_path.parent / BACKUP_FOLDER_NAME
            stem = project_slug("", database.project().name)
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            candidate = folder / f"{stem}_{stamp}{PROJECT_EXTENSION}"
            sequence = 2
            while candidate.exists():
                candidate = folder / f"{stem}_{stamp}_{sequence:02d}{PROJECT_EXTENSION}"
                sequence += 1
            return snapshot_database(database.path, candidate, replace=False)

    def close_current(self, *, save: bool = True) -> None:
        with self._lock:
            if self._current is None:
                return
            if save:
                self.save_current()
            if self._lease:
                self._lease.release()
            self._current = None
            self._source_path = None
            self._lease = None
            self._dirty = False
            self._lease_error = ""

    def current_payload(self) -> dict[str, Any] | None:
        with self._lock:
            if self._current is None or self._source_path is None:
                return None
            project = self._current.project_dict()
            project["database_path"] = str(self._source_path)
            return {
                "project": project,
                "designConditions": self._current.design_conditions(),
                "modelDesignState": self._current.model_design_state(),
                "workingPath": str(self._current.path),
                "dirty": self._dirty,
                "lockPath": str(self._lease.path) if self._lease else "",
                "lockHealthy": bool(self._lease and self._lease.acquired and not self._lease_error),
                "lockMessage": self._lease_error,
            }

    def bootstrap_payload(self) -> dict[str, Any]:
        return {
            "application": {
                "name": "WRL FlumeWorks",
                "version": __version__,
                "gitCommit": self.commit,
            },
            "projectExtension": PROJECT_EXTENSION,
            "facilities": [{"id": key, "name": name} for key, name in FACILITIES.items()],
            "recentProjects": self.recents.list(),
            "currentProject": self.current_payload(),
            "modelDesignUrl": self.model_design_url,
        }

    def suggested_directory(self) -> str:
        current = self._source_path
        if current:
            return str(current.parent)
        for item in self.recents.list():
            path = Path(str(item.get("path", "")))
            if path.parent.is_dir():
                return str(path.parent)
        return str(self.settings.project_root)

    def shutdown(self) -> None:
        self._stop_heartbeat.set()
        self._heartbeat.join(timeout=2)
        with self._lock:
            self.close_current(save=True)

