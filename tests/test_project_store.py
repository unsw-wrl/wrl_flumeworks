from __future__ import annotations

from pathlib import Path

import pytest

from flumeworks.project_store import (
    PROJECT_FILENAME,
    ProjectCatalog,
    ProjectError,
    ProjectExistsError,
)


def test_create_project_and_store_design_conditions(tmp_path: Path) -> None:
    catalog = ProjectCatalog(tmp_path)
    database = catalog.create(
        name="Bronte seawall physical model",
        project_number="WRL2023067",
        facility="flume_3m",
        description="A project database separate from the model diary.",
    )

    assert database.path.name == PROJECT_FILENAME
    assert database.project().facility_name == "3 m wave flume"
    assert database.project().project_number == "WRL2023067"

    condition = database.add_design_condition(
        condition_number="2",
        target_hs_m=6.9,
        target_tp_s=14.2,
        water_level_m_ahd=1.58,
        aep_percent=1.0,
        ari_years=100.0,
    )

    assert condition["condition_number"] == "2"
    assert database.design_conditions()[0]["target_hs_m"] == pytest.approx(6.9)
    assert catalog.list()[0]["uuid"] == database.project().uuid


def test_project_creation_refuses_to_touch_non_empty_folder(tmp_path: Path) -> None:
    occupied = tmp_path / "WRL0001_Existing_project"
    occupied.mkdir()
    sentinel = occupied / "keep-me.txt"
    sentinel.write_text("untouched", encoding="utf-8")

    with pytest.raises(ProjectExistsError, match="nothing was overwritten"):
        ProjectCatalog(tmp_path).create(
            name="Existing project",
            project_number="WRL0001",
            facility="flume_0_9m",
        )

    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert not (occupied / PROJECT_FILENAME).exists()


def test_design_condition_number_is_unique(tmp_path: Path) -> None:
    database = ProjectCatalog(tmp_path).create(
        name="Uniqueness test",
        project_number="WRL0002",
        facility="wave_basin",
    )
    database.add_design_condition(condition_number="1")

    with pytest.raises(ProjectError, match="already exists"):
        database.add_design_condition(condition_number="1")

