from __future__ import annotations

from pathlib import Path
from typing import Any

from .app_state import ApplicationState
from .project_store import PROJECT_EXTENSION, project_slug


class DesktopApi:
    """Small native-dialog bridge exposed to the pywebview interface."""

    def __init__(self, state: ApplicationState):
        # pywebview recursively walks every public attribute on its JS API object.
        # Keep implementation objects private so the native Window/WinForms tree is
        # never mistaken for an API namespace.
        self._state = state
        self._window: Any = None

    def _bind_window(self, window: Any) -> None:
        self._window = window

    def _directory(self) -> str:
        suggested = Path(self._state.suggested_directory())
        if suggested.is_dir():
            return str(suggested)
        documents = Path.home() / "Documents"
        return str(documents if documents.is_dir() else Path.home())

    def choose_new_project(self, project_number: str = "", project_name: str = "") -> str | None:
        if self._window is None:
            return None
        import webview

        try:
            stem = project_slug(project_number, project_name)
        except ValueError:
            stem = "new_flumeworks_project"
        selected = self._window.create_file_dialog(
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
        if self._window is None:
            return None
        import webview

        selected = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=self._directory(),
            allow_multiple=False,
            file_types=("FlumeWorks project (*.flumeworks)",),
        )
        return str(selected[0]) if selected else None
