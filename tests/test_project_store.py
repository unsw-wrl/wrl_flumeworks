from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from flumeworks.project_store import (
    ProjectDatabase,
    ProjectExistsError,
    ProjectLease,
    ProjectLockedError,
    RecentProjects,
    snapshot_database,
)


def create_database(path: Path) -> ProjectDatabase:
    return ProjectDatabase.create(
        path,
        name="Bronte seawall physical model",
        project_number="WRL2023067",
        facility="flume_3m",
        description="A project database separate from the model diary.",
    )


def test_project_and_model_design_state_round_trip(tmp_path: Path) -> None:
    database = create_database(tmp_path / "arbitrary folder" / "Bronte.flumeworks")
    model_design = {
        "cadDrawing": {"sourceFile": "3m_flume.dwg"},
        "bathymetry": {"points": [{"chainage": 0.0, "elevation": 0.4}]},
        "waveConditions": {"conditions": [{"conditionId": "1", "waveHeight": 6.9}]},
    }

    database.save_model_design_state(model_design)
    database.add_design_condition(condition_number="2", target_hs_m=6.9, target_tp_s=14.2)

    assert database.project().facility_name == "3 m wave flume"
    assert database.model_design_state() == model_design
    assert database.design_conditions()[0]["target_hs_m"] == pytest.approx(6.9)


def test_snapshot_refuses_to_overwrite_backup(tmp_path: Path) -> None:
    database = create_database(tmp_path / "source.flumeworks")
    destination = tmp_path / "flumeworks_backups" / "snapshot.flumeworks"
    snapshot_database(database.path, destination, replace=False)

    with pytest.raises(ProjectExistsError, match="nothing was overwritten"):
        snapshot_database(database.path, destination, replace=False)


def test_project_lease_blocks_a_second_editor_and_releases(tmp_path: Path) -> None:
    project = tmp_path / "shared.flumeworks"
    first = ProjectLease(project, app_version="test")
    second = ProjectLease(project, app_version="test")
    first.acquire()

    with pytest.raises(ProjectLockedError, match="locked by"):
        second.acquire()

    first.release()
    second.acquire()
    second.release()
    assert not project.with_name(project.name + ".lock").exists()


def test_version_one_database_migrates_without_losing_project_data(tmp_path: Path) -> None:
    path = tmp_path / "old.flumeworks"
    database = create_database(path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("DROP TABLE model_design_state")
        connection.execute("DELETE FROM schema_migration")
        connection.execute("INSERT INTO schema_migration(version, applied_at) VALUES (1, 'test')")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    migrated = ProjectDatabase(path)

    assert migrated.project().name == "Bronte seawall physical model"
    assert migrated.model_design_state() is None
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_recent_projects_remember_arbitrary_paths(tmp_path: Path) -> None:
    project = tmp_path / "anywhere" / "project.flumeworks"
    database = create_database(project)
    recents = RecentProjects(tmp_path / "local-state")

    recents.add(database, project)

    assert recents.list()[0]["path"] == str(project.resolve())
    assert recents.list()[0]["available"] is True
