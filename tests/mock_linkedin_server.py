from __future__ import annotations


import http.server
import threading
import urllib.parse
from pathlib import Path

MOCK_DIR = Path(__file__).resolve().parent / "mock_linkedin"


class _MockLinkedInHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(MOCK_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path in ("/index.html", "/") and query.get("fail") == ["session"]:
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, format, *args):
        pass  # keep test output quiet


def start_mock_server() -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockLinkedInHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}"
