from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from flumeworks.api import create_app
from flumeworks.app_state import ApplicationState
from flumeworks.settings import Settings


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(project_root=tmp_path / "projects", state_root=tmp_path / "state", model_config={})
    state = ApplicationState(settings, "http://127.0.0.1:54321/", tmp_path)
    return TestClient(create_app(state))


def test_project_workflow_api(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["modelDesignUrl"] == "http://127.0.0.1:54321/"
    assert bootstrap.json()["currentProject"] is None

    created = client.post(
        "/api/projects",
        json={
            "name": "API project",
            "project_number": "WRL0099",
            "facility": "flume_1_2m",
            "description": "Created by the API test",
        },
    )
    assert created.status_code == 201
    assert created.json()["currentProject"]["project"]["facility_name"] == "1.2 m wave flume"

    condition = client.post(
        "/api/design-conditions",
        json={
            "condition_number": "DC1",
            "target_hs_m": 4.2,
            "target_tp_s": 12.5,
            "water_level_m_ahd": 0.7,
            "aep_percent": 2,
            "ari_years": 50,
            "notes": "Design condition",
        },
    )
    assert condition.status_code == 201
    assert condition.json()["currentProject"]["designConditions"][0]["condition_number"] == "DC1"


def test_design_condition_requires_an_open_project(tmp_path: Path) -> None:
    response = make_client(tmp_path).post(
        "/api/design-conditions",
        json={"condition_number": "1"},
    )

    assert response.status_code == 400
    assert "Create or open a project" in response.json()["detail"]


def test_frontend_shell_is_served(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/")

    assert response.status_code == 200
    assert "WRL FlumeWorks" in response.text
    assert "Model Design" in response.text

