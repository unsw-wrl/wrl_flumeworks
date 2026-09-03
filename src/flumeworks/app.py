from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

from .api import create_app
from .app_state import ApplicationState
from .desktop_api import DesktopApi
from .model_design_runtime import ModelDesignRuntime
from .server_runtime import ApplicationServer
from .settings import load_settings


def _ask_save_before_closing(window: object) -> bool:
    """Show a native Yes/No prompt without enabling pywebview's blanket quit dialog."""
    if sys.platform == "win32" and getattr(window, "native", None) is not None:
        from webview.platforms.winforms import WinForms

        result = WinForms.MessageBox.Show(
            window.native,
            "Do you want to save before closing?",
            "Unsaved project",
            WinForms.MessageBoxButtons.YesNo,
            WinForms.MessageBoxIcon.Question,
        )
        return result == WinForms.DialogResult.Yes
    return bool(
        window.create_confirmation_dialog(
            "Unsaved project",
            "Do you want to save before closing?",
        )
    )


def handle_desktop_close(
    state: ApplicationState,
    window: object,
    ask_to_save=None,
) -> bool | None:
    """Prepare state for a native close; returning False cancels pywebview closing."""
    current = state.current_payload()
    if current is None:
        return None
    try:
        save = (
            bool(ask_to_save(window) if ask_to_save else _ask_save_before_closing(window))
            if current["dirty"]
            else False
        )
        state.close_current(save=save)
    except Exception as exc:
        try:
            window.create_confirmation_dialog(
                "Could not close FlumeWorks",
                f"The project could not be saved and closed:\n\n{exc}",
            )
        except Exception:
            print(f"The project could not be saved and closed: {exc}", file=sys.stderr)
        return False
    return None


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

            desktop_api = DesktopApi(state)
            window = webview.create_window(
                "WRL FlumeWorks",
                application_server.url,
                js_api=desktop_api,
                width=1500,
                height=950,
                min_size=(1050, 700),
                confirm_close=False,
            )
            desktop_api._bind_window(window)
            window.events.closing += lambda: handle_desktop_close(state, window)
            webview.start()
        return 0
    finally:
        try:
            state.shutdown()
        except Exception as exc:
            print(f"Warning: the active project could not be saved during shutdown: {exc}", file=sys.stderr)
        application_server.stop()
        model_design.stop()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
