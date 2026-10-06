"""CLI entrypoint for running the Telegram Cleaner application."""

import sys
import uvicorn


def main() -> None:
    """Run uvicorn server for Telegram Cleaner."""
    host = "127.0.0.1"
    port = 8000
    print(f"Starting Telegram Cleaner at http://{host}:{port}")
    uvicorn.run("tg_cleaner.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
