"""Entry point для запуска через python -m src.

Запускает uvicorn сервер с FastAPI приложением.
Бот запускается автоматически как фоновая задача.

Использование:
    python -m src              # Production mode (без hot-reload)
    python -m src --dev        # Development mode (с hot-reload)
    python -m src --help       # Показать справку
"""

import argparse

import uvicorn


def main() -> None:
    """Запустить приложение через uvicorn."""
    parser = argparse.ArgumentParser(
        description="Unibot — Telegram-бот для AI-генерации контента",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
    python -m src              # Production mode
    python -m src --dev        # Development mode с hot-reload
    python -m src --port 3000  # Указать кастомный порт
        """,
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Включить hot-reload для разработки",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Хост для сервера (по умолчанию: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Порт для сервера (по умолчанию: 8000)",
    )
    args = parser.parse_args()

    if args.dev:
        # Development mode с hot-reload
        uvicorn.run(
            "src.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            reload_includes=["src/**/*.py"],
            reload_excludes=[".venv/**", "data/**", "tests/**", ".git/**"],
        )
    else:
        # Production mode без hot-reload
        uvicorn.run(
            "src.main:app",
            host=args.host,
            port=args.port,
            reload=False,
        )


if __name__ == "__main__":
    main()
