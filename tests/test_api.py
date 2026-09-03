from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flumeworks.api import create_app
from flumeworks.app_state import ApplicationState
from flumeworks.settings import Settings


def make_client(tmp_path: Path) -> tuple[TestClient, ApplicationState]:
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", model_config={})
    state = ApplicationState(settings, "http://127.0.0.1:54321/", tmp_path)
    return TestClient(create_app(state)), state


def test_project_model_design_save_and_backup_workflow(tmp_path: Path) -> None:
    client, state = make_client(tmp_path)
    project_path = tmp_path / "selected" / "API project.flumeworks"
    try:
        bootstrap = client.get("/api/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["recentProjects"] == []
        assert bootstrap.json()["currentProject"] is None

        created = client.post(
            "/api/projects",
            json={
                "destination_path": str(project_path),
                "name": "API project",
                "project_number": "WRL0099",
                "facility": "flume_1_2m",
                "description": "Created by the API test",
            },
        )
        assert created.status_code == 201
        assert created.json()["currentProject"]["project"]["database_path"] == str(project_path)

        model_design = {
            "cadDrawing": {"sourceFile": "wave_flume.dwg"},
            "bathymetry": {"points": [{"chainage": 0, "elevation": 0.4}]},
            "waveConditions": {"conditions": [{"conditionId": "1"}]},
        }
        saved_model = client.put("/api/model-design", json={"state": model_design})
        assert saved_model.status_code == 200
        assert saved_model.json()["currentProject"]["modelDesignState"] == model_design

        condition = client.post(
            "/api/design-conditions",
            json={"condition_number": "DC1", "target_hs_m": 4.2, "target_tp_s": 12.5, "water_level_m_ahd": 1.2, "wave_stats_depth_m_ahd": -15, "aep_percent": 4},
        )
        assert condition.status_code == 201
        assert condition.json()["condition"]["ari_years"] == pytest.approx(25.0)

        condition_id = condition.json()["condition"]["id"]
        updated = client.put(
            f"/api/design-conditions/{condition_id}",
            json={"condition_number": "DC1", "target_hs_m": 4.4, "target_tp_s": 12.6, "water_level_m_ahd": 1.3, "wave_stats_depth_m_ahd": -14.5, "ari_years": 20},
        )
        assert updated.status_code == 200
        assert updated.json()["condition"]["target_hs_m"] == 4.4
        assert updated.json()["condition"]["aep_percent"] == pytest.approx(5.0)

        imported = client.put(
            "/api/design-conditions/import",
            json={"source_filename": "wave_conditions.csv", "conditions": [{"condition_number": "4", "target_hs_m": 6.9, "target_tp_s": 14.2, "water_level_m_ahd": 1.58, "wave_stats_depth_m_ahd": -15, "ari_years": 600}]},
        )
        assert imported.status_code == 200
        assert imported.json()["currentProject"]["project"]["wave_conditions_filename"] == "wave_conditions.csv"
        assert imported.json()["currentProject"]["designConditions"][0]["aep_percent"] == pytest.approx(100 / 600)

        scaled = client.put("/api/project-scale", json={"denominator": 50})
        assert scaled.status_code == 200
        assert scaled.json()["currentProject"]["project"]["model_scale_denominator"] == 50

        imported_id = imported.json()["currentProject"]["designConditions"][0]["id"]
        deleted = client.delete(f"/api/design-conditions/{imported_id}")
        assert deleted.status_code == 200
        assert deleted.json()["currentProject"]["designConditions"] == []

        saved = client.post("/api/projects/save")
        assert saved.status_code == 200
        assert saved.json()["currentProject"]["dirty"] is False

        backup = client.post("/api/projects/backup")
        assert backup.status_code == 200
        assert Path(backup.json()["backupPath"]).is_file()

        closed = client.post("/api/projects/close")
        assert closed.status_code == 200
        assert not project_path.with_name(project_path.name + ".lock").exists()
    finally:
        state.shutdown()


def test_design_condition_requires_an_open_project(tmp_path: Path) -> None:
    client, state = make_client(tmp_path)
    try:
        response = client.post("/api/design-conditions", json={"condition_number": "1"})
        assert response.status_code == 400
        assert "Create or open a project" in response.json()["detail"]
    finally:
        state.shutdown()


def test_frontend_shell_is_served(tmp_path: Path) -> None:
    client, state = make_client(tmp_path)
    try:
        response = client.get("/")
        assert response.status_code == 200
        assert "WRL FlumeWorks" in response.text
        assert "Generate backup" in response.text
        assert "Model Design" in response.text
    finally:
        state.shutdown()
