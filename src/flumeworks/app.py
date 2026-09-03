from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

from .api import create_app
from .app_state import ApplicationState
from .model_design_runtime import ModelDesignRuntime
from .server_runtime import ApplicationServer
from .settings import load_settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WRL FlumeWorks desktop application")
    parser.add_argument("--browser", action="store_true", help="Open in the system browser instead of pywebview")
    parser.add_argument("--project-root", type=Path, help="Folder containing FlumeWorks project directories")
    parser.add_argument("--config", type=Path, help="Local model executable configuration JSON")
    parser.add_argument("--port", type=int, default=0, help="Local application port; 0 selects a free port")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args.project_root, args.config)
    model_design = ModelDesignRuntime(settings.model_config)
    model_design.start()
    repository_root = Path(__file__).resolve().parents[2]
    state = ApplicationState(settings, model_design.url, repository_root)
    application_server = ApplicationServer(create_app(state), port=args.port)
    try:
        application_server.start()
        print(f"WRL FlumeWorks: {application_server.url}")
        print(f"Model Design: {model_design.url}")
        print(f"Projects: {settings.project_root}")
        if args.browser:
            webbrowser.open(application_server.url)
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                return 0
        else:
            import webview

            webview.create_window(
                "WRL FlumeWorks",
                application_server.url,
                width=1500,
                height=950,
                min_size=(1050, 700),
                confirm_close=True,
            )
            webview.start()
        return 0
    finally:
        application_server.stop()
        model_design.stop()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()

