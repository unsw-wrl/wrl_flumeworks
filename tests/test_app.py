from __future__ import annotations

from typing import Any

from flumeworks.app import handle_desktop_close


class FakeState:
    def __init__(self, current: dict[str, Any] | None):
        self.current = current
        self.close_calls: list[bool] = []

    def current_payload(self) -> dict[str, Any] | None:
        return self.current

    def close_current(self, *, save: bool = True) -> None:
        self.close_calls.append(save)
        self.current = None


class FakeWindow:
    def create_confirmation_dialog(self, title: str, message: str) -> bool:
        raise AssertionError("No fallback dialog was expected")


def test_clean_project_closes_without_prompt_or_save() -> None:
    state = FakeState({"dirty": False})
    prompted = False

    def ask_to_save(_window: object) -> bool:
        nonlocal prompted
        prompted = True
        return True

    result = handle_desktop_close(state, FakeWindow(), ask_to_save)

    assert result is None
    assert prompted is False
    assert state.close_calls == [False]


def test_dirty_project_saves_when_user_chooses_yes() -> None:
    state = FakeState({"dirty": True})

    result = handle_desktop_close(state, FakeWindow(), lambda _window: True)

    assert result is None
    assert state.close_calls == [True]


def test_dirty_project_discards_when_user_chooses_no() -> None:
    state = FakeState({"dirty": True})

    result = handle_desktop_close(state, FakeWindow(), lambda _window: False)

    assert result is None
    assert state.close_calls == [False]
