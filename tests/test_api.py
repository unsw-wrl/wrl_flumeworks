from __future__ import annotations

from pathlib import Path

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
            json={"condition_number": "DC1", "target_hs_m": 4.2, "target_tp_s": 12.5},
        )
        assert condition.status_code == 201

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

