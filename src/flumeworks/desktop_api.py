from __future__ import annotations

from pathlib import Path
from typing import Any

from .app_state import ApplicationState
from .project_store import PROJECT_EXTENSION, project_slug


class DesktopApi:
    """Small native-dialog bridge exposed to the pywebview interface."""

    def __init__(self, state: ApplicationState):
        self.state = state
        self.window: Any = None

    def bind_window(self, window: Any) -> None:
        self.window = window

    def _directory(self) -> str:
        suggested = Path(self.state.suggested_directory())
        if suggested.is_dir():
            return str(suggested)
        documents = Path.home() / "Documents"
        return str(documents if documents.is_dir() else Path.home())

    def choose_new_project(self, project_number: str = "", project_name: str = "") -> str | None:
        if self.window is None:
            return None
        import webview

        try:
            stem = project_slug(project_number, project_name)
        except ValueError:
            stem = "new_flumeworks_project"
        selected = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=self._directory(),
            save_filename=stem + PROJECT_EXTENSION,
            file_types=("FlumeWorks project (*.flumeworks)",),
        )
        if not selected:
            return None
        path = Path(selected[0])
        if path.suffix.lower() != PROJECT_EXTENSION:
            path = path.with_suffix(PROJECT_EXTENSION)
        return str(path)

    def choose_open_project(self) -> str | None:
        if self.window is None:
            return None
        import webview

        selected = self.window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=self._directory(),
            allow_multiple=False,
            file_types=("FlumeWorks project (*.flumeworks)",),
        )
        return str(selected[0]) if selected else None

