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
    condition = database.add_design_condition(
        condition_number="2",
        target_hs_m=6.9,
        target_tp_s=14.2,
        water_level_m_ahd=1.58,
        wave_stats_depth_m_ahd=-15.0,
    )
    database.update_design_condition(
        condition["id"],
        condition_number="2",
        target_hs_m=7.0,
        target_tp_s=14.3,
        water_level_m_ahd=1.6,
        wave_stats_depth_m_ahd=-14.5,
        ari_years=100,
    )
    database.set_model_scale(50)

    assert database.project().facility_name == "3 m wave flume"
    assert database.model_design_state() == model_design
    assert database.project().model_scale_denominator == pytest.approx(50)
    assert database.design_conditions()[0]["target_hs_m"] == pytest.approx(7.0)
    assert database.design_conditions()[0]["wave_stats_depth_m_ahd"] == pytest.approx(-14.5)
    assert database.design_conditions()[0]["aep_percent"] == pytest.approx(1.0)
    assert database.design_conditions()[0]["ari_years"] == pytest.approx(100.0)


def test_import_replaces_conditions_and_records_csv_name(tmp_path: Path) -> None:
    database = create_database(tmp_path / "import.flumeworks")
    database.add_design_condition(condition_number="old")
    database.replace_design_conditions(
        [
            {"condition_number": "1", "target_hs_m": 4.2, "target_tp_s": 11.2, "water_level_m_ahd": 0.67, "wave_stats_depth_m_ahd": -15.0, "ari_years": 1},
            {"condition_number": "2", "target_hs_m": 5.6, "target_tp_s": 12.5, "water_level_m_ahd": 1.34, "wave_stats_depth_m_ahd": -15.0, "ari_years": 10},
        ],
        source_filename=r"C:\inputs\wave_conditions.csv",
    )

    conditions = database.design_conditions()
    assert [item["condition_number"] for item in conditions] == ["1", "2"]
    assert conditions[0]["aep_percent"] == pytest.approx(100.0)
    assert conditions[1]["aep_percent"] == pytest.approx(10.0)
    assert database.project().wave_conditions_filename == "wave_conditions.csv"
    database.delete_design_condition(database.design_conditions()[0]["id"])
    assert [item["condition_number"] for item in database.design_conditions()] == ["2"]


def test_aep_derives_ari_for_direct_condition_entry(tmp_path: Path) -> None:
    database = create_database(tmp_path / "probability.flumeworks")

    condition = database.add_design_condition(condition_number="AEP only", aep_percent=4)

    assert condition["aep_percent"] == pytest.approx(4.0)
    assert condition["ari_years"] == pytest.approx(25.0)


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


@pytest.mark.parametrize("old_version", [1, 2])
def test_older_database_migrates_without_losing_project_data(tmp_path: Path, old_version: int) -> None:
    path = tmp_path / f"old-v{old_version}.flumeworks"
    database = create_database(path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("ALTER TABLE design_condition DROP COLUMN wave_stats_depth_m_ahd")
        connection.execute("ALTER TABLE project DROP COLUMN wave_conditions_filename")
        connection.execute("ALTER TABLE project DROP COLUMN model_scale_denominator")
        if old_version == 1:
            connection.execute("DROP TABLE model_design_state")
        connection.execute("DELETE FROM schema_migration")
        connection.execute("INSERT INTO schema_migration(version, applied_at) VALUES (?, 'test')", (old_version,))
        connection.execute(f"PRAGMA user_version = {old_version}")
        connection.commit()

    migrated = ProjectDatabase(path)

    assert migrated.project().name == "Bronte seawall physical model"
    assert migrated.model_design_state() is None
    assert migrated.project().model_scale_denominator is None
    assert migrated.project().wave_conditions_filename == ""
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_recent_projects_remember_arbitrary_paths(tmp_path: Path) -> None:
    project = tmp_path / "anywhere" / "project.flumeworks"
    database = create_database(project)
    recents = RecentProjects(tmp_path / "local-state")

    recents.add(database, project)

    assert recents.list()[0]["path"] == str(project.resolve())
    assert recents.list()[0]["available"] is True
