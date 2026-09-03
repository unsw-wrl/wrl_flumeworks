from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .app_state import ApplicationState
from .project_store import ProjectError, ProjectExistsError, ProjectLockedError


PACKAGE_ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = PACKAGE_ROOT / "frontend"


class ProjectCreateRequest(BaseModel):
    destination_path: str = Field(min_length=1, max_length=2000)
    name: str = Field(min_length=1, max_length=200)
    project_number: str = Field(default="", max_length=100)
    facility: str
    description: str = Field(default="", max_length=4000)


class ProjectOpenRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=2000)


class DesignConditionRequest(BaseModel):
    condition_number: str = Field(min_length=1, max_length=100)
    target_hs_m: float | None = Field(default=None, ge=0)
    target_tp_s: float | None = Field(default=None, gt=0)
    water_level_m_ahd: float | None = None
    aep_percent: float | None = Field(default=None, gt=0, le=100)
    ari_years: float | None = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=4000)


class ModelDesignStateRequest(BaseModel):
    state: dict[str, Any]


def project_http_error(exc: ProjectError) -> HTTPException:
    if isinstance(exc, ProjectLockedError):
        return HTTPException(status_code=423, detail={"message": str(exc), "owner": exc.owner})
    if isinstance(exc, ProjectExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def create_app(state: ApplicationState) -> FastAPI:
    app = FastAPI(title="WRL FlumeWorks", version="0.2.0", docs_url=None, redoc_url=None)
    app.mount("/assets", StaticFiles(directory=FRONTEND_ROOT / "assets"), name="assets")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "application": state.bootstrap_payload()["application"]}

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, object]:
        return state.bootstrap_payload()

    @app.post("/api/projects", status_code=201)
    def create_project(request: ProjectCreateRequest) -> dict[str, object]:
        try:
            current = state.create_project(**request.model_dump())
        except ProjectError as exc:
            raise project_http_error(exc) from exc
        return {"currentProject": current, "recentProjects": state.recents.list()}

    @app.post("/api/projects/open")
    def open_project(request: ProjectOpenRequest) -> dict[str, object]:
        try:
            current = state.open_project(request.source_path)
        except ProjectError as exc:
            raise project_http_error(exc) from exc
        return {"currentProject": current, "recentProjects": state.recents.list()}

    @app.post("/api/projects/save")
    def save_project() -> dict[str, object]:
        try:
            current = state.save_current()
        except ProjectError as exc:
            raise project_http_error(exc) from exc
        return {"currentProject": current, "recentProjects": state.recents.list()}

    @app.post("/api/projects/backup")
    def backup_project() -> dict[str, object]:
        try:
            backup = state.backup_current()
        except (OSError, ProjectError) as exc:
            error = exc if isinstance(exc, ProjectError) else ProjectError(str(exc))
            raise project_http_error(error) from exc
        return {"backupPath": str(backup), "currentProject": state.current_payload()}

    @app.post("/api/projects/close")
    def close_project() -> dict[str, object]:
        try:
            state.close_current(save=True)
        except ProjectError as exc:
            raise project_http_error(exc) from exc
        return {"currentProject": None, "recentProjects": state.recents.list()}

    @app.post("/api/design-conditions", status_code=201)
    def add_design_condition(request: DesignConditionRequest) -> dict[str, object]:
        try:
            condition = state.add_design_condition(**request.model_dump())
        except ProjectError as exc:
            raise project_http_error(exc) from exc
        return {"condition": condition, "currentProject": state.current_payload()}

    @app.put("/api/model-design")
    def save_model_design(request: ModelDesignStateRequest) -> dict[str, object]:
        try:
            current = state.save_model_design(request.state)
        except ProjectError as exc:
            raise project_http_error(exc) from exc
        return {"currentProject": current}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_ROOT / "index.html")

    return app

