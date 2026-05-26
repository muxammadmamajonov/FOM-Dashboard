#!/usr/bin/env python3
"""Minimal local web server for the FOM dashboard.

Serves the single ``uploads/index.html`` file — the exact file the Telegram bot
atomically overwrites on each upload — with ``no-store`` cache headers, so a
freshly uploaded dashboard is reflected on the very next page load. Designed to
sit behind a ``cloudflared`` tunnel.

Security: only the dashboard itself is exposed. Every other path (including the
``backups/`` folder) returns 404, so upload history is never publicly browsable.

Usage:
    python serve.py                 # serves ./uploads/index.html on :8080
    python serve.py --port 9000     # custom port
    python serve.py --dir ./uploads # custom upload folder
"""

from __future__ import annotations

import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_PLACEHOLDER = (
    b"<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
    b"<title>FOM Dashboard</title></head>"
    b"<body style='font-family:sans-serif;text-align:center;margin-top:15vh'>"
    b"<h1>Dashboard not published yet</h1>"
    b"<p>Waiting for the administrator to upload the first HTML file.</p>"
    b"</body></html>"
)


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves only ``index.html`` (read fresh from disk on every request)."""

    index_path: Path = Path("uploads/index.html")
    server_version = "FOMDashboard/1.0"

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Never cache: always reflect the latest uploaded dashboard.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            # read_bytes() each time -> picks up the bot's os.replace instantly.
            if self.index_path.is_file():
                self._send(200, self.index_path.read_bytes())
            else:
                self._send(200, _PLACEHOLDER)
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    do_HEAD = do_GET  # HEAD shares GET logic; _send skips the body itself.

    def log_message(self, fmt: str, *args) -> None:
        """Compact access log to stdout."""
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the FOM dashboard.")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080).")
    parser.add_argument(
        "--dir",
        default=os.getenv("UPLOAD_FOLDER_PATH", "./uploads"),
        help="Upload folder containing index.html (default ./uploads).",
    )
    args = parser.parse_args()

    DashboardHandler.index_path = (
        Path(args.dir).expanduser().resolve() / "index.html"
    )
    # Bind to localhost only; cloudflared connects locally and exposes it.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Serving {DashboardHandler.index_path}")
    print(f"  -> http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
