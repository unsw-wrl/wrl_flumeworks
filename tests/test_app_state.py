from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from flumeworks.app_state import ApplicationState
from flumeworks.project_store import ProjectLockedError
from flumeworks.settings import Settings


def make_state(tmp_path: Path, suffix: str = "one") -> ApplicationState:
    settings = Settings(
        project_root=tmp_path,
        state_root=tmp_path / f"state-{suffix}",
        model_config={},
    )
    return ApplicationState(settings, "http://127.0.0.1:54321/", tmp_path)


def create_project(state: ApplicationState, path: Path) -> dict:
    return state.create_project(
        destination_path=str(path),
        name="Arbitrary location project",
        project_number="WRL0099",
        facility="flume_1_2m",
        description="Local working-copy test",
    )


def test_local_working_copy_save_backup_and_close(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    source = tmp_path / "chosen folder" / "My project.flumeworks"
    try:
        current = create_project(state, source)
        working = Path(current["workingPath"])
        lock = source.with_name(source.name + ".lock")

        assert source.is_file()
        assert working.is_file() and working != source
        assert lock.is_file()

        state.add_design_condition(condition_number="4", target_hs_m=6.9)
        state.save_model_design(
            {
                "cadDrawing": {"sourceFile": "3m_flume.dwg"},
                "bathymetry": {"points": [{"chainage": 0, "elevation": 0.4}]},
                "waveConditions": {"conditions": [{"conditionId": "4"}]},
            }
        )
        with closing(sqlite3.connect(source)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM design_condition").fetchone()[0] == 0

        saved = state.save_current()
        assert saved["dirty"] is False
        with closing(sqlite3.connect(source)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM design_condition").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM model_design_state").fetchone()[0] == 1

        first_backup = state.backup_current()
        second_backup = state.backup_current()
        assert first_backup.parent == source.parent / "flumeworks_backups"
        assert first_backup.name.startswith("Arbitrary_location_project_")
        assert second_backup.stem.endswith("_02")

        state.close_current(save=True)
        assert not lock.exists()
        assert state.current_payload() is None
    finally:
        state.shutdown()


def test_second_application_is_blocked_by_project_lock(tmp_path: Path) -> None:
    first = make_state(tmp_path, "first")
    second = make_state(tmp_path, "second")
    source = tmp_path / "shared.flumeworks"
    try:
        create_project(first, source)
        with pytest.raises(ProjectLockedError):
            second.open_project(str(source))
    finally:
        first.shutdown()
        second.shutdown()


def test_project_details_and_file_location_update_safely(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    source = tmp_path / "original" / "project.flumeworks"
    destination = tmp_path / "relocated" / "renamed.flumeworks"
    try:
        create_project(state, source)
        state.add_design_condition(condition_number="DC1", target_hs_m=3.2)

        current = state.update_current_project(
            destination_path=str(destination),
            name="Relocated project",
            project_number="WRL2042",
            facility="flume_3m",
            model_scale_denominator=40,
            description="Relocated project description",
        )

        assert destination.is_file()
        assert source.is_file()
        assert not source.with_name(source.name + ".lock").exists()
        assert destination.with_name(destination.name + ".lock").is_file()
        assert current["dirty"] is False
        assert current["project"]["database_path"] == str(destination.resolve())
        assert current["project"]["name"] == "Relocated project"
        assert current["project"]["model_scale_denominator"] == pytest.approx(40)
        assert current["project"]["description"] == "Relocated project description"
        assert [item["condition_number"] for item in current["designConditions"]] == ["DC1"]

        with closing(sqlite3.connect(source)) as connection:
            assert connection.execute("SELECT name FROM project WHERE id = 1").fetchone()[0] == "Arbitrary location project"
            assert connection.execute("SELECT COUNT(*) FROM design_condition").fetchone()[0] == 0
        with closing(sqlite3.connect(destination)) as connection:
            assert connection.execute("SELECT name FROM project WHERE id = 1").fetchone()[0] == "Relocated project"
            assert connection.execute("SELECT COUNT(*) FROM design_condition").fetchone()[0] == 1
    finally:
        state.shutdown()
