"""Запуск Unibot с перезапуском при изменении файла .env."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
POLL_INTERVAL_SECONDS = 1.0


def start_server() -> subprocess.Popen[str]:
    """Запустить Uvicorn в текущем виртуальном окружении."""
    child_env = os.environ.copy()
    for key in tuple(child_env):
        if key.split("__", 1)[0] in {
            "APP",
            "BOT",
            "AI",
            "LOGGING",
            "DATABASE",
            "ADMIN",
            "FSM",
            "PAYMENTS",
            "CHANNEL",
            "PROXY",
        }:
            child_env.pop(key)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        cwd=PROJECT_ROOT,
        env=child_env,
        text=True,
    )


def stop_server(child: subprocess.Popen[str]) -> None:
    """Остановить дочерний процесс Uvicorn."""
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def main() -> int:
    """Перезапускать приложение после изменения .env."""
    child = start_server()
    last_mtime = ENV_FILE.stat().st_mtime_ns if ENV_FILE.exists() else 0

    try:
        while child.poll() is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            current_mtime = ENV_FILE.stat().st_mtime_ns if ENV_FILE.exists() else 0
            if current_mtime == last_mtime:
                continue

            last_mtime = current_mtime
            sys.stderr.write("Изменён .env — перезапуск приложения\n")
            stop_server(child)
            child = start_server()
    except KeyboardInterrupt:
        stop_server(child)
        return 0

    return child.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
