"""
LiningOps Copilot — Native Windows Desktop Client
Connects to Central AWS EC2 Server (http://65.1.125.127) or Local FastAPI Sidecar.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

DEFAULT_EC2_SERVER = "http://65.1.125.127"
APP_TITLE = "LiningOps Copilot — Refractory Engineering Desktop"


def resolve_ui_dir() -> Path:
    """Locate the local ui/ folder whether running from source or PyInstaller bundle."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ui"
    here = Path(__file__).resolve().parent
    if (here / "ui").exists():
        return here / "ui"
    return here.parent / "ui"


def check_server_reachable(server_url: str, timeout: float = 3.0) -> bool:
    """Ping the /health endpoint of the central EC2 server."""
    url = server_url.rstrip("/") + "/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LiningOps-Desktop/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_free_port(preferred: int = 8000) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_local_sidecar(port: int) -> None:
    """Start local FastAPI uvicorn server in a background daemon thread (All-in-One mode)."""
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    import uvicorn
    from app.main import app

    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "127.0.0.1", "port": port, "log_level": "warning"},
        daemon=True,
    )
    thread.start()
    for _ in range(40):
        if check_server_reachable(f"http://127.0.0.1:{port}", timeout=0.5):
            break
        time.sleep(0.25)


def launch_edge_or_chrome_app_window(target_url: str) -> bool:
    """Fallback native OS app-mode window using Edge or Chrome if pywebview is not installed."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for exe in candidates:
        if os.path.exists(exe):
            subprocess.Popen([exe, f"--app={target_url}", "--start-maximized"])
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument(
        "--server",
        default=os.environ.get("REFRACTORY_SERVER_URL", DEFAULT_EC2_SERVER),
        help=f"Central server URL (default: {DEFAULT_EC2_SERVER})",
    )
    parser.add_argument(
        "--local-sidecar",
        action="store_true",
        help="Start a local FastAPI backend sidecar on 127.0.0.1 instead of using EC2",
    )
    parser.add_argument(
        "--local-ui",
        action="store_true",
        help="Load UI HTML from local disk while sending API requests to --server",
    )
    args = parser.parse_args()

    server_url = args.server.rstrip("/")

    if args.local_sidecar:
        port = find_free_port(8000)
        server_url = f"http://127.0.0.1:{port}"
        print(f"[Desktop] Starting local FastAPI sidecar on {server_url} ...")
        start_local_sidecar(port)

    ui_dir = resolve_ui_dir()
    local_app_html = ui_dir / "app.html"

    # Privacy-first client architecture:
    # Always run the UI, 14-step workflow state, formula engine (fx.js), and local session storage
    # directly from the client's local disk (file:///.../ui/app.html?api=<server_url>).
    # The remote server is only invoked for backend model inference (/api/v1/extraction/process and /api/v1/lining/generate).
    if local_app_html.exists():
        target_url = f"{local_app_html.as_uri()}?api={server_url}"
        print(f"[Desktop] Running Local Client UI (Private Local Storage) -> Backend Model API: {server_url}")
    else:
        target_url = f"{server_url}/app"
        print(f"[Desktop] Fallback to Remote Server UI: {server_url}")

    try:
        import webview  # pywebview (uses native Windows Edge WebView2)

        window = webview.create_window(
            title=APP_TITLE,
            url=target_url,
            width=1540,
            height=940,
            min_size=(1180, 740),
            resizable=True,
            text_select=True,
            confirm_close=False,
        )
        webview.start(debug=False)
    except ImportError:
        print("[Desktop] pywebview not installed; launching native Edge/Chrome App Window...")
        if not launch_edge_or_chrome_app_window(target_url):
            import webbrowser
            webbrowser.open(target_url)


if __name__ == "__main__":
    main()
