from __future__ import annotations

from pathlib import Path

from flumeworks.desktop_api import DesktopApi


class FakeState:
    def __init__(self, directory: Path):
        self.directory = directory

    def suggested_directory(self) -> str:
        return str(self.directory)


class FakeWindow:
    def __init__(self, selected: Path):
        self.selected = selected
        self.calls: list[tuple[object, dict[str, object]]] = []

    def create_file_dialog(self, dialog_type: object, **options: object) -> tuple[str]:
        self.calls.append((dialog_type, options))
        return (str(self.selected),)


def test_only_dialog_methods_are_exposed_to_pywebview(tmp_path: Path) -> None:
    api = DesktopApi(FakeState(tmp_path))  # type: ignore[arg-type]
    api._bind_window(FakeWindow(tmp_path / "selected project"))

    assert all(name.startswith("_") for name in vars(api))
    assert api.choose_new_project("WRL001", "Test") == str(
        tmp_path / "selected project.flumeworks"
    )
