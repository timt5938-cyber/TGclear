"""Main entrypoint and launcher for Telegram Cleaner.

Starts the FastAPI backend application via Uvicorn, finds an available port,
and opens the default web browser to the local web UI.
"""

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

# Configure sys.path so 'tg_cleaner' can be imported seamlessly
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def is_port_available(host: str, port: int) -> bool:
    """Check if a TCP port is available for binding on the specified host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_available_port(host: str, preferred_port: int = 8765, max_attempts: int = 100) -> int:
    """Find an available port starting from preferred_port."""
    for port in range(preferred_port, preferred_port + max_attempts):
        if is_port_available(host, port):
            return port
    raise RuntimeError(
        f"Could not find an available port on {host} between {preferred_port} and {preferred_port + max_attempts}"
    )


def open_browser_delayed(url: str, delay: float = 1.0) -> None:
    """Open the web browser after a short delay."""
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception as exc:
        print(f"[Launcher] Notice: Could not open browser automatically: {exc}")


def run_test_boot(host: str, port: int) -> None:
    """Boot the server in a background thread, verify HTTP GET /, and shut down cleanly."""
    print(f"[Test Boot] Starting Telegram Cleaner server on http://{host}:{port} for boot verification...")

    config = uvicorn.Config(
        "tg_cleaner.api.app:app",
        host=host,
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    target_url = f"http://{host}:{port}/"
    timeout = 10.0
    start_time = time.time()
    boot_successful = False

    while time.time() - start_time < timeout:
        time.sleep(0.5)
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "TestBootVerification/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as response:
                if response.status == 200:
                    body = response.read()
                    print(
                        f"[Test Boot] SUCCESS: Server is responsive at {target_url} (HTTP {response.status}, {len(body)} bytes)."
                    )
                    boot_successful = True
                    break
        except Exception:
            continue

    # Gracefully signal server to stop
    server.should_exit = True
    server_thread.join(timeout=3.0)

    if not boot_successful:
        print(f"[Test Boot] ERROR: Server failed to respond at {target_url} within {timeout}s.", file=sys.stderr)
        sys.exit(1)

    print("[Test Boot] Verification passed cleanly.")
    sys.exit(0)


def main() -> None:
    """Application main entrypoint."""
    parser = argparse.ArgumentParser(description="Telegram Cleaner Launcher")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Preferred port to bind (default: 8765, auto-increments if occupied)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open web browser automatically on startup",
    )
    parser.add_argument(
        "--test-boot",
        action="store_true",
        help="Run server boot test, verify HTTP GET /, and exit cleanly",
    )
    args = parser.parse_args()

    host = args.host
    try:
        port = find_available_port(host, preferred_port=args.port)
    except Exception as exc:
        print(f"[Launcher] Error determining port: {exc}", file=sys.stderr)
        sys.exit(1)

    if port != args.port:
        print(f"[Launcher] Port {args.port} was busy. Selected alternative port {port}.")

    # Handle test-boot mode
    if args.test_boot:
        run_test_boot(host, port)
        return

    app_url = f"http://{host}:{port}"
    print("=" * 70)
    print("                      Telegram Cleaner")
    print("=" * 70)
    print(f" Application URL:  {app_url}")
    print(f" Working Directory:{Path(__file__).resolve().parent}")
    print(f" Source Directory: {SRC_DIR}")
    print(" Press Ctrl+C in this console window to stop the server.")
    print("=" * 70)

    if not args.no_browser:
        browser_thread = threading.Thread(
            target=open_browser_delayed,
            args=(app_url, 1.0),
            daemon=True,
        )
        browser_thread.start()

    try:
        uvicorn.run(
            "tg_cleaner.api.app:app",
            host=host,
            port=port,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n[Launcher] Shutdown requested by user (Ctrl+C). Exiting cleanly...")
        sys.exit(0)


if __name__ == "__main__":
    main()
