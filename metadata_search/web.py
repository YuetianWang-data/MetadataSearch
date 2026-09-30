"""A localhost-only HTTP adapter for the existing search engine."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit
import webbrowser
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from .loader import load_catalog
from .query import Settings
from .search import SearchEngine


INPUT_ERRORS = (OSError, ValueError, BadZipFile, InvalidFileException, ParseError)
PAGE = Path(__file__).resolve().parents[1] / "index.html"


class Catalog:
    def __init__(self, directory: Path, config: Path):
        self.directory = directory
        self.config = config
        self.reload()

    def reload(self):
        # Replace the active catalog only after the entire import succeeds.
        settings = Settings.load(self.config)
        records, files = load_catalog(self.directory)
        engine = SearchEngine(records, settings)
        self.engine, self.files = engine, files

    def status(self):
        return {
            "systems": len(self.files), "fields": len(self.engine.records),
            "files": self.files, "data_directory": str(self.directory),
            "weights": self.engine.settings.weights,
        }


def make_handler(catalog: Catalog):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            # Do not log URLs: they may include private search queries.
            sys.stderr.write(f"{self.command} request from {self.client_address[0]}\n")

        def respond(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def json_response(self, status: int, data: dict):
            self.respond(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

        def valid_host(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.json_response(403, {"error": "Only localhost access is allowed"})
                return False
            return True

        def do_GET(self):
            if not self.valid_host():
                return
            url = urlsplit(self.path)
            if url.path == "/":
                try:
                    content = PAGE.read_bytes()
                except OSError as error:
                    self.json_response(500, {"error": f"Cannot read the search page: {error}"})
                    return
                self.respond(200, content, "text/html; charset=utf-8")
            elif url.path == "/favicon.ico":
                self.respond(204, b"", "image/x-icon")
            elif url.path == "/api/status":
                self.json_response(200, catalog.status())
            elif url.path == "/api/search":
                try:
                    params = parse_qs(url.query, max_num_fields=10)
                    result = catalog.engine.search(
                        params.get("q", [""])[0],
                        int(params.get("page", ["1"])[0]),
                        int(params.get("page_size", ["20"])[0]),
                    )
                except ValueError as error:
                    self.json_response(400, {"error": str(error)})
                    return
                self.json_response(200, result)
            else:
                self.json_response(404, {"error": "Not found"})

        def do_POST(self):
            if not self.valid_host():
                return
            if urlsplit(self.path).path != "/api/reload":
                self.json_response(404, {"error": "Not found"})
                return
            origin = self.headers.get("Origin")
            if (self.headers.get("X-Metadata-Request") != "1"
                    or (origin and origin != f"http://{self.headers.get('Host')}")):
                self.json_response(403, {"error": "Reload must be requested from the local search page"})
                return
            try:
                catalog.reload()
            except INPUT_ERRORS as error:
                self.json_response(400, {"error": f"Reload failed; previous catalog retained: {error}"})
                return
            self.json_response(200, catalog.status())

    return Handler


def serve(directory: Path, config: Path, port: int, open_browser: bool = False):
    catalog = Catalog(directory, config)
    # Single-threaded request handling keeps reloads and searches consistent.
    with HTTPServer(("127.0.0.1", port), make_handler(catalog)) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Metadata search: {url}\nPress Ctrl+C to stop.", flush=True)
        if open_browser and not webbrowser.open(url):
            print("Could not open a browser automatically. Open the URL above manually.", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
