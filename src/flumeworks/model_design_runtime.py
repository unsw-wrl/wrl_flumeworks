from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .model_design import wave_model_service as service


def _runtime_paths(config: dict[str, Any], key: str) -> tuple[str, ...]:
    return service.normalise_runtime_paths(config.get(key), key)


def build_engines(config: dict[str, Any]) -> dict[str, service.Engine]:
    common = _runtime_paths(config, "runtimePaths")
    return {
        "swan": service.Engine(
            "SWAN",
            service.discover_executable(config.get("swanExecutable"), ("swan.exe", "swan")),
            True,
            "1D stationary spectral transformation to the seawall toe",
            service.merge_runtime_paths(common, _runtime_paths(config, "swanRuntimePaths")),
        ),
        "swash": service.Engine(
            "SWASH",
            service.discover_executable(config.get("swashExecutable"), ("swash.exe", "swash")),
            True,
            "1D non-hydrostatic phase-resolving transformation to the seawall toe",
            service.merge_runtime_paths(common, _runtime_paths(config, "swashRuntimePaths")),
        ),
        "xbeach": service.Engine(
            "XBeach",
            service.discover_executable(config.get("xbeachExecutable"), ("xbeach.exe", "xbeach")),
            True,
            "1D surfbeat transformation with short-wave breaking, setup, and infragravity-wave effects",
            service.merge_runtime_paths(common, _runtime_paths(config, "xbeachRuntimePaths")),
        ),
    }


class ModelDesignRuntime:
    def __init__(self, model_config: dict[str, Any]):
        root = Path(service.__file__).resolve().parent
        self.viewer = root / "wave_flume_bathymetry_viewer.html"
        self.runs = root / "wave_model_runs"
        self.engines = build_engines(model_config)
        self.server = service.WaveModelServer(("127.0.0.1", 0), self.viewer, self.runs, self.engines)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="flumeworks-model-design",
            daemon=True,
        )

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=5)
        self.server.server_close()

