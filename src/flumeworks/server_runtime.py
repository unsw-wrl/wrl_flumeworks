from __future__ import annotations

import socket
import threading
import time

import uvicorn
from fastapi import FastAPI


def available_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


class ApplicationServer:
    def __init__(self, app: FastAPI, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port or available_port(host)
        config = uvicorn.Config(app, host=host, port=self.port, log_level="warning", access_log=False)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="flumeworks-api", daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self, timeout: float = 10) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("The FlumeWorks local application server did not start.")

    def stop(self) -> None:
        if self.thread.is_alive():
            self.server.should_exit = True
            self.thread.join(timeout=5)

