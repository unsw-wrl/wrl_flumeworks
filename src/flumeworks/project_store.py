from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PROJECT_FILENAME = "project.flumeworks"
FACILITIES = {
    "flume_0_9m": "0.9 m wave flume",
    "flume_1_2m": "1.2 m wave flume",
    "flume_3m": "3 m wave flume",
    "wave_basin": "Wave basin",
}


class ProjectError(ValueError):
    """Base error returned to the application interface."""


class ProjectExistsError(ProjectError):
    """Raised when project creation would overwrite existing content."""


@dataclass(frozen=True)
class ProjectRecord:
    uuid: str
    name: str
    project_number: str
    facility: str
    facility_name: str
    description: str
    created_at: str
    updated_at: str
    database_path: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def project_slug(project_number: str, name: str) -> str:
    source = "_".join(part for part in (project_number.strip(), name.strip()) if part)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", source).strip("._-")
    if not slug:
        raise ProjectError("Project name or project number must contain a letter or number.")
    return slug[:100]


SCHEMA_SQL = """
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE project (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    project_number TEXT NOT NULL DEFAULT '',
    facility TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE design_condition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL DEFAULT 1 REFERENCES project(id) ON DELETE CASCADE,
    condition_number TEXT NOT NULL,
    target_hs_m REAL CHECK (target_hs_m IS NULL OR target_hs_m >= 0),
    target_tp_s REAL CHECK (target_tp_s IS NULL OR target_tp_s > 0),
    water_level_m_ahd REAL,
    aep_percent REAL CHECK (aep_percent IS NULL OR (aep_percent > 0 AND aep_percent <= 100)),
    ari_years REAL CHECK (ari_years IS NULL OR ari_years > 0),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, condition_number)
);

CREATE TABLE application_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    app_version TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    computer_name TEXT NOT NULL,
    user_name TEXT NOT NULL
);

CREATE INDEX design_condition_project_idx ON design_condition(project_id, condition_number);
"""


class ProjectDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ProjectError(f"Project database does not exist: {self.path}")
        self._verify()

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        name: str,
        project_number: str,
        facility: str,
        description: str = "",
    ) -> "ProjectDatabase":
        destination = Path(path).resolve()
        if destination.exists():
            raise ProjectExistsError(f"Project database already exists: {destination}")
        if facility not in FACILITIES:
            raise ProjectError("Select a recognised WRL physical-model facility.")
        clean_name = name.strip()
        if not clean_name:
            raise ProjectError("Project name is required.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(destination)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA_SQL)
            now = utc_now()
            connection.execute(
                "INSERT INTO schema_migration(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, now),
            )
            connection.execute(
                """INSERT INTO project
                   (id, uuid, name, project_number, facility, description, created_at, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), clean_name, project_number.strip(), facility, description.strip(), now, now),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.close()
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        else:
            connection.close()
        return cls(destination)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _verify(self) -> None:
        try:
            with self._connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                row = connection.execute("SELECT uuid FROM project WHERE id = 1").fetchone()
        except sqlite3.Error as exc:
            raise ProjectError(f"Could not open FlumeWorks project {self.path}: {exc}") from exc
        if version != SCHEMA_VERSION or row is None:
            raise ProjectError(
                f"Unsupported FlumeWorks project schema {version}; this application expects {SCHEMA_VERSION}."
            )

    def project(self) -> ProjectRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM project WHERE id = 1").fetchone()
        if row is None:
            raise ProjectError("Project metadata is missing.")
        return ProjectRecord(
            uuid=row["uuid"],
            name=row["name"],
            project_number=row["project_number"],
            facility=row["facility"],
            facility_name=FACILITIES.get(row["facility"], row["facility"]),
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            database_path=str(self.path),
        )

    def project_dict(self) -> dict[str, Any]:
        return asdict(self.project())

    def design_conditions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, condition_number, target_hs_m, target_tp_s,
                          water_level_m_ahd, aep_percent, ari_years, notes,
                          created_at, updated_at
                   FROM design_condition
                   ORDER BY CAST(condition_number AS REAL), condition_number"""
            ).fetchall()
        return [dict(row) for row in rows]

    def add_design_condition(
        self,
        *,
        condition_number: str,
        target_hs_m: float | None = None,
        target_tp_s: float | None = None,
        water_level_m_ahd: float | None = None,
        aep_percent: float | None = None,
        ari_years: float | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        number = condition_number.strip()
        if not number:
            raise ProjectError("Design condition number is required.")
        now = utc_now()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """INSERT INTO design_condition
                       (condition_number, target_hs_m, target_tp_s, water_level_m_ahd,
                        aep_percent, ari_years, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        number,
                        target_hs_m,
                        target_tp_s,
                        water_level_m_ahd,
                        aep_percent,
                        ari_years,
                        notes.strip(),
                        now,
                        now,
                    ),
                )
                connection.execute("UPDATE project SET updated_at = ? WHERE id = 1", (now,))
                row = connection.execute(
                    "SELECT * FROM design_condition WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise ProjectError(f"Design condition {number} already exists.") from exc
            raise ProjectError(f"Design condition values are invalid: {exc}") from exc
        return dict(row) if row else {}

    def record_application_run(
        self,
        *,
        app_version: str,
        git_commit: str,
        computer_name: str,
        user_name: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO application_run
                   (started_at, app_version, git_commit, computer_name, user_name)
                   VALUES (?, ?, ?, ?, ?)""",
                (utc_now(), app_version, git_commit, computer_name, user_name),
            )


class ProjectCatalog:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def create(
        self,
        *,
        name: str,
        project_number: str,
        facility: str,
        description: str = "",
    ) -> ProjectDatabase:
        folder = self.root / project_slug(project_number, name)
        database_path = folder / PROJECT_FILENAME
        if database_path.exists() or (folder.exists() and any(folder.iterdir())):
            raise ProjectExistsError(
                f"Project folder already contains files; nothing was overwritten: {folder}"
            )
        return ProjectDatabase.create(
            database_path,
            name=name,
            project_number=project_number,
            facility=facility,
            description=description,
        )

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        projects: list[dict[str, Any]] = []
        for path in sorted(self.root.glob(f"*/{PROJECT_FILENAME}")):
            try:
                projects.append(ProjectDatabase(path).project_dict())
            except ProjectError:
                continue
        return sorted(projects, key=lambda item: item["updated_at"], reverse=True)

    def open_uuid(self, project_uuid: str) -> ProjectDatabase:
        for project in self.list():
            if project["uuid"] == project_uuid:
                return ProjectDatabase(project["database_path"])
        raise ProjectError("The selected project was not found in the configured project folder.")

