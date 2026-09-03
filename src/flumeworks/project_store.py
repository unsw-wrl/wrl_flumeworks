from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3
PROJECT_EXTENSION = ".flumeworks"
BACKUP_FOLDER_NAME = "flumeworks_backups"
FACILITIES = {
    "flume_0_9m": "0.9 m wave flume",
    "flume_1_2m": "1.2 m wave flume",
    "flume_3m": "3 m wave flume",
    "wave_basin": "Wave basin",
}


class ProjectError(ValueError):
    """Base error returned to the application interface."""


class ProjectExistsError(ProjectError):
    """Raised when project creation would overwrite an existing file."""


class ProjectLockedError(ProjectError):
    """Raised when another FlumeWorks session owns the project lease."""

    def __init__(self, path: Path, owner: dict[str, Any]):
        self.path = path
        self.owner = owner
        user = owner.get("userName") or "another user"
        computer = owner.get("computerName") or "another computer"
        opened = owner.get("openedAt") or "an unknown time"
        super().__init__(f"This project is locked by {user} on {computer} (opened {opened}).")


@dataclass(frozen=True)
class ProjectRecord:
    uuid: str
    name: str
    project_number: str
    facility: str
    facility_name: str
    description: str
    model_scale_denominator: float | None
    wave_conditions_filename: str
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


def normalise_project_path(path: str | Path) -> Path:
    value = Path(path).expanduser()
    if value.suffix.lower() != PROJECT_EXTENSION:
        value = value.with_suffix(PROJECT_EXTENSION)
    return value.resolve()


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
    model_scale_denominator REAL CHECK (model_scale_denominator IS NULL OR model_scale_denominator > 0),
    wave_conditions_filename TEXT NOT NULL DEFAULT '',
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
    wave_stats_depth_m_ahd REAL,
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

CREATE TABLE model_design_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX design_condition_project_idx ON design_condition(project_id, condition_number);
"""


MIGRATION_1_TO_2 = """
CREATE TABLE model_design_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


MIGRATION_2_TO_3 = """
ALTER TABLE project ADD COLUMN model_scale_denominator REAL
    CHECK (model_scale_denominator IS NULL OR model_scale_denominator > 0);
ALTER TABLE project ADD COLUMN wave_conditions_filename TEXT NOT NULL DEFAULT '';
ALTER TABLE design_condition ADD COLUMN wave_stats_depth_m_ahd REAL;
"""


class ProjectDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ProjectError(f"Project database does not exist: {self.path}")
        self._verify_and_migrate()

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
            raise ProjectExistsError(f"Project file already exists; nothing was overwritten: {destination}")
        if facility not in FACILITIES:
            raise ProjectError("Select a recognised WRL physical-model facility.")
        clean_name = name.strip()
        if not clean_name:
            raise ProjectError("Project name is required.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(destination)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
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
            destination.unlink(missing_ok=True)
            raise
        else:
            connection.close()
        return cls(destination)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _verify_and_migrate(self) -> None:
        try:
            with closing(self._connect()) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version == 1:
                    connection.executescript(MIGRATION_1_TO_2)
                    connection.execute(
                        "INSERT INTO schema_migration(version, applied_at) VALUES (?, ?)",
                        (2, utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 2")
                    connection.commit()
                    version = 2
                if version == 2:
                    connection.executescript(MIGRATION_2_TO_3)
                    connection.execute(
                        "INSERT INTO schema_migration(version, applied_at) VALUES (?, ?)",
                        (3, utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 3")
                    connection.commit()
                    version = 3
                row = connection.execute("SELECT uuid FROM project WHERE id = 1").fetchone()
        except sqlite3.Error as exc:
            raise ProjectError(f"Could not open FlumeWorks project {self.path}: {exc}") from exc
        if version != SCHEMA_VERSION or row is None:
            raise ProjectError(
                f"Unsupported FlumeWorks project schema {version}; this application expects {SCHEMA_VERSION}."
            )

    def project(self) -> ProjectRecord:
        with closing(self._connect()) as connection:
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
            model_scale_denominator=row["model_scale_denominator"],
            wave_conditions_filename=row["wave_conditions_filename"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            database_path=str(self.path),
        )

    def project_dict(self) -> dict[str, Any]:
        return asdict(self.project())

    def design_conditions(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT id, condition_number, target_hs_m, target_tp_s,
                          water_level_m_ahd, wave_stats_depth_m_ahd,
                          aep_percent, ari_years, notes,
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
        wave_stats_depth_m_ahd: float | None = None,
        aep_percent: float | None = None,
        ari_years: float | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        number = condition_number.strip()
        if not number:
            raise ProjectError("Design condition number is required.")
        now = utc_now()
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """INSERT INTO design_condition
                       (condition_number, target_hs_m, target_tp_s, water_level_m_ahd,
                        wave_stats_depth_m_ahd, aep_percent, ari_years, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        number,
                        target_hs_m,
                        target_tp_s,
                        water_level_m_ahd,
                        wave_stats_depth_m_ahd,
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
                connection.commit()
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise ProjectError(f"Design condition {number} already exists.") from exc
            raise ProjectError(f"Design condition values are invalid: {exc}") from exc
        return dict(row) if row else {}

    def update_design_condition(
        self,
        condition_id: int,
        *,
        condition_number: str,
        target_hs_m: float | None = None,
        target_tp_s: float | None = None,
        water_level_m_ahd: float | None = None,
        wave_stats_depth_m_ahd: float | None = None,
        aep_percent: float | None = None,
        ari_years: float | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        number = condition_number.strip()
        if not number:
            raise ProjectError("Design condition number is required.")
        now = utc_now()
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """UPDATE design_condition
                       SET condition_number = ?, target_hs_m = ?, target_tp_s = ?,
                           water_level_m_ahd = ?, wave_stats_depth_m_ahd = ?,
                           aep_percent = ?, ari_years = ?, notes = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        number,
                        target_hs_m,
                        target_tp_s,
                        water_level_m_ahd,
                        wave_stats_depth_m_ahd,
                        aep_percent,
                        ari_years,
                        notes.strip(),
                        now,
                        condition_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProjectError("That design condition no longer exists.")
                connection.execute("UPDATE project SET updated_at = ? WHERE id = 1", (now,))
                row = connection.execute(
                    "SELECT * FROM design_condition WHERE id = ?", (condition_id,)
                ).fetchone()
                connection.commit()
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise ProjectError(f"Design condition {number} already exists.") from exc
            raise ProjectError(f"Design condition values are invalid: {exc}") from exc
        return dict(row) if row else {}

    def delete_design_condition(self, condition_id: int) -> None:
        now = utc_now()
        with closing(self._connect()) as connection:
            cursor = connection.execute("DELETE FROM design_condition WHERE id = ?", (condition_id,))
            if cursor.rowcount != 1:
                raise ProjectError("That design condition no longer exists.")
            connection.execute("UPDATE project SET updated_at = ? WHERE id = 1", (now,))
            connection.commit()

    def replace_design_conditions(
        self, conditions: list[dict[str, Any]], *, source_filename: str = ""
    ) -> None:
        if not conditions:
            raise ProjectError("The wave-condition CSV contains no valid conditions.")
        now = utc_now()
        seen: set[str] = set()
        try:
            with closing(self._connect()) as connection:
                connection.execute("DELETE FROM design_condition")
                for condition in conditions:
                    number = str(condition.get("condition_number", "")).strip()
                    if not number:
                        raise ProjectError("Every design condition requires a condition number.")
                    identity = number.casefold()
                    if identity in seen:
                        raise ProjectError(f"Design condition {number} appears more than once.")
                    seen.add(identity)
                    connection.execute(
                        """INSERT INTO design_condition
                           (condition_number, target_hs_m, target_tp_s, water_level_m_ahd,
                            wave_stats_depth_m_ahd, aep_percent, ari_years, notes,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            number,
                            condition.get("target_hs_m"),
                            condition.get("target_tp_s"),
                            condition.get("water_level_m_ahd"),
                            condition.get("wave_stats_depth_m_ahd"),
                            condition.get("aep_percent"),
                            condition.get("ari_years"),
                            str(condition.get("notes", "")).strip(),
                            now,
                            now,
                        ),
                    )
                connection.execute(
                    """UPDATE project
                       SET wave_conditions_filename = ?, updated_at = ?
                       WHERE id = 1""",
                    (Path(source_filename).name, now),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ProjectError(f"Wave-condition CSV values are invalid: {exc}") from exc

    def set_model_scale(self, denominator: float | None) -> None:
        if denominator is not None and denominator <= 0:
            raise ProjectError("Project scale must be greater than zero.")
        now = utc_now()
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE project
                   SET model_scale_denominator = ?, updated_at = ?
                   WHERE id = 1""",
                (denominator, now),
            )
            connection.commit()

    def model_design_state(self) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM model_design_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise ProjectError("The saved Model Design state is invalid.") from exc
        return payload if isinstance(payload, dict) else None

    def save_model_design_state(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ProjectError("Model Design state must be a JSON object.")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 20_000_000:
            raise ProjectError("Model Design state is larger than the 20 MB project limit.")
        now = utc_now()
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO model_design_state(id, payload_json, updated_at)
                   VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,
                                                 updated_at=excluded.updated_at""",
                (encoded, now),
            )
            connection.execute("UPDATE project SET updated_at = ? WHERE id = 1", (now,))
            connection.commit()

    def record_application_run(
        self,
        *,
        app_version: str,
        git_commit: str,
        computer_name: str,
        user_name: str,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO application_run
                   (started_at, app_version, git_commit, computer_name, user_name)
                   VALUES (?, ?, ?, ?, ?)""",
                (utc_now(), app_version, git_commit, computer_name, user_name),
            )
            connection.commit()


def snapshot_database(source: str | Path, destination: str | Path, *, replace: bool) -> Path:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_file():
        raise ProjectError(f"Project source does not exist: {source_path}")
    if destination_path.exists() and not replace:
        raise ProjectExistsError(f"Backup already exists; nothing was overwritten: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(f".{destination_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        source_connection = sqlite3.connect(source_path)
        destination_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(destination_connection)
            result = destination_connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ProjectError("The project snapshot failed its database integrity check.")
        finally:
            destination_connection.close()
            source_connection.close()
        if destination_path.exists() and not replace:
            raise ProjectExistsError(f"Backup already exists; nothing was overwritten: {destination_path}")
        if replace:
            os.replace(temporary, destination_path)
        else:
            temporary.rename(destination_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination_path


class ProjectLease:
    def __init__(self, project_path: str | Path, *, app_version: str):
        self.project_path = Path(project_path).resolve()
        self.path = self.project_path.with_name(self.project_path.name + ".lock")
        self.token = uuid.uuid4().hex
        now = utc_now()
        self.payload: dict[str, Any] = {
            "format": "wrl-flumeworks-lock",
            "token": self.token,
            "projectPath": str(self.project_path),
            "userName": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
            "computerName": socket.gethostname(),
            "processId": os.getpid(),
            "appVersion": app_version,
            "openedAt": now,
            "lastHeartbeat": now,
        }
        self.acquired = False

    @staticmethod
    def read_owner(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(self.payload, indent=2) + "\n"
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ProjectLockedError(self.project_path, self.read_owner(self.path)) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        self.acquired = True

    def refresh(self) -> None:
        if not self.acquired:
            return
        owner = self.read_owner(self.path)
        if owner.get("token") != self.token:
            self.acquired = False
            raise ProjectLockedError(self.project_path, owner)
        self.payload["lastHeartbeat"] = utc_now()
        temporary = self.path.with_name(f".{self.path.name}.{self.token}.tmp")
        temporary.write_text(json.dumps(self.payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def release(self) -> None:
        if not self.acquired:
            return
        owner = self.read_owner(self.path)
        if owner.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self.acquired = False


class RecentProjects:
    def __init__(self, state_root: str | Path):
        self.path = Path(state_root).resolve() / "recent_projects.json"

    def _read(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def list(self) -> list[dict[str, Any]]:
        result = []
        for item in self._read():
            path = Path(str(item.get("path", "")))
            result.append({**item, "available": path.is_file()})
        return result

    def add(self, database: ProjectDatabase, source_path: str | Path) -> None:
        project = database.project()
        source = str(Path(source_path).resolve())
        item = {
            "uuid": project.uuid,
            "name": project.name,
            "project_number": project.project_number,
            "facility_name": project.facility_name,
            "path": source,
            "lastOpened": utc_now(),
        }
        existing = [entry for entry in self._read() if str(entry.get("path", "")).lower() != source.lower()]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([item, *existing][:20], indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def local_working_path(state_root: str | Path, source_path: str | Path) -> Path:
    source = str(Path(source_path).resolve()).lower().encode("utf-8")
    identity = hashlib.sha256(source).hexdigest()[:20]
    return Path(state_root).resolve() / "workspaces" / identity / "working.flumeworks"
