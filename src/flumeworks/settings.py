from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Settings:
    project_root: Path
    state_root: Path
    model_config: dict[str, Any]

    @property
    def model_runs_root(self) -> Path:
        # The imported model-design service currently requires its run folder
        # beneath its own module directory. This is deliberately isolated and
        # ignored by Git until the adapter is separated in a later milestone.
        return Path(__file__).resolve().parent / "model_design" / "wave_model_runs"


def default_state_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "WRL" / "FlumeWorks" if base else Path.home() / ".wrl_flumeworks"


def default_project_root() -> Path:
    return Path.home() / "Documents" / "WRL FlumeWorks Projects"


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Configuration {path} must contain a JSON object.")
    return value


def load_settings(
    project_root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> Settings:
    state_root = default_state_root()
    selected_config: Path | None = Path(config_path).expanduser() if config_path else None
    if selected_config is None:
        candidates = [Path.cwd() / "flumeworks_config.json", state_root / "config.json"]
        selected_config = next((path for path in candidates if path.is_file()), None)
    model_config = _read_json_object(selected_config.resolve()) if selected_config else {}
    configured_root = project_root or model_config.get("projectRoot")
    root = Path(configured_root).expanduser() if configured_root else default_project_root()
    return Settings(project_root=root.resolve(), state_root=state_root.resolve(), model_config=model_config)

