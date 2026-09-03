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


def test_project_details_can_be_updated_together(tmp_path: Path) -> None:
    database = create_database(tmp_path / "details.flumeworks")

    database.update_project(
        name="Updated project title",
        project_number="WRL2042",
        facility="flume_0_9m",
        model_scale_denominator=35,
        description="Updated description",
    )

    project = database.project()
    assert project.name == "Updated project title"
    assert project.project_number == "WRL2042"
    assert project.facility == "flume_0_9m"
    assert project.facility_name == "0.9 m wave flume"
    assert project.model_scale_denominator == pytest.approx(35)
    assert project.description == "Updated description"


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


def test_condition_table_order_is_persisted_and_can_be_empty(tmp_path: Path) -> None:
    database = create_database(tmp_path / "ordered.flumeworks")
    database.replace_design_conditions(
        [
            {"condition_number": "10", "target_hs_m": 5.0},
            {"condition_number": "2", "target_hs_m": 4.0},
        ],
        allow_empty=True,
    )

    assert [item["condition_number"] for item in database.design_conditions()] == ["10", "2"]
    assert [item["sort_order"] for item in database.design_conditions()] == [0, 1]

    database.replace_design_conditions([], allow_empty=True)
    assert database.design_conditions() == []


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


@pytest.mark.parametrize("old_version", [1, 2, 3])
def test_older_database_migrates_without_losing_project_data(tmp_path: Path, old_version: int) -> None:
    path = tmp_path / f"old-v{old_version}.flumeworks"
    database = create_database(path)
    database.add_design_condition(condition_number="10")
    database.add_design_condition(condition_number="2")
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("DROP INDEX design_condition_sort_idx")
        connection.execute("ALTER TABLE design_condition DROP COLUMN sort_order")
        if old_version < 3:
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
    assert [item["condition_number"] for item in migrated.design_conditions()] == ["2", "10"]
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert "sort_order" in {row[1] for row in connection.execute("PRAGMA table_info(design_condition)")}


def test_recent_projects_remember_arbitrary_paths(tmp_path: Path) -> None:
    project = tmp_path / "anywhere" / "project.flumeworks"
    database = create_database(project)
    recents = RecentProjects(tmp_path / "local-state")

    recents.add(database, project)

    assert recents.list()[0]["path"] == str(project.resolve())
    assert recents.list()[0]["available"] is True

    recents.remove(project)
    assert recents.list() == []
