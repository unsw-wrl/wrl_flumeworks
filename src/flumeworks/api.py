from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .app_state import ApplicationState
from .project_store import ProjectError, ProjectExistsError


PACKAGE_ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = PACKAGE_ROOT / "frontend"


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_number: str = Field(default="", max_length=100)
    facility: str
    description: str = Field(default="", max_length=4000)


class ProjectOpenRequest(BaseModel):
    project_uuid: str = Field(min_length=1, max_length=100)


class DesignConditionRequest(BaseModel):
    condition_number: str = Field(min_length=1, max_length=100)
    target_hs_m: float | None = Field(default=None, ge=0)
    target_tp_s: float | None = Field(default=None, gt=0)
    water_level_m_ahd: float | None = None
    aep_percent: float | None = Field(default=None, gt=0, le=100)
    ari_years: float | None = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=4000)


def create_app(state: ApplicationState) -> FastAPI:
    app = FastAPI(title="WRL FlumeWorks", version="0.1.0", docs_url=None, redoc_url=None)
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
        except ProjectExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"currentProject": current, "projects": state.catalog.list()}

    @app.post("/api/projects/open")
    def open_project(request: ProjectOpenRequest) -> dict[str, object]:
        try:
            current = state.open_project(request.project_uuid)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"currentProject": current}

    @app.post("/api/design-conditions", status_code=201)
    def add_design_condition(request: DesignConditionRequest) -> dict[str, object]:
        try:
            condition = state.add_design_condition(**request.model_dump())
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"condition": condition, "currentProject": state.current_payload()}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_ROOT / "index.html")

    return app

