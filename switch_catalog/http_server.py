from __future__ import annotations

import base64
import html
import hmac
import mimetypes
import os
import re
import socket
import sqlite3
import threading
from dataclasses import dataclass
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from .paths import APP_DIR, DB_PATH
from .settings import AppSettings

_CHUNK = 256 * 1024
_DL_PATTERN = re.compile(r"^/dl/(game|update)/(\d+)(?:/.*)?$")
_LIST_PATHS = {"/list.txt", "/awoo.txt"}


@dataclass(frozen=True)
class CatalogFile:
    kind: str
    id: int
    file_path: str
    file_name: str
    file_size: int
    modified_time: float


class CatalogHttpServer:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        if self._server is not None:
            return int(self._server.server_address[1])
        return self._port

    def start(self, settings: AppSettings) -> None:
        self.stop()
        self._port = int(settings.http_server_port)
        handler = _handler_factory(
            self.db_path,
            settings.http_server_username.strip(),
            settings.http_server_password,
        )
        server = ThreadingHTTPServer(("", self._port), handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="CatalogHttpServer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)


def switch_directory_url(port: int) -> str:
    return f"http://{local_lan_ip()}:{int(port or 8000)}/dir/"


def local_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _handler_factory(db_path: Path, username: str, password: str) -> type[BaseHTTPRequestHandler]:
    class CatalogRequestHandler(BaseHTTPRequestHandler):
        server_version = "SwitchGameCatalog"
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._handle_request(head_only=False)

        def do_HEAD(self) -> None:
            self._handle_request(head_only=True)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            try:
                range_header = self.headers.get("Range", "-") if self.headers else "-"
                with (APP_DIR / "server.log").open("a", encoding="utf-8") as handle:
                    handle.write(f"{self.log_date_time_string()} {format % args} Range={range_header}\n")
            except Exception:
                pass

        def _handle_request(self, *, head_only: bool) -> None:
            if not self._authorized():
                self._send_unauthorized()
                return

            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in {"/", "/dir"}:
                self._send_directory_listing(head_only=head_only)
                return
            if path in _LIST_PATHS:
                self._send_url_list(head_only=head_only)
                return
            match = _DL_PATTERN.match(path)
            if match:
                self._send_download_by_id(match.group(1), int(match.group(2)), head_only=head_only)
                return
            if path.startswith("/dir/"):
                self._send_download_by_name(path.removeprefix("/dir/"), head_only=head_only)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def _authorized(self) -> bool:
            if not password:
                return True
            value = self.headers.get("Authorization", "")
            if not value.startswith("Basic "):
                return False
            try:
                decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return False
            expected = f"{username}:{password}"
            return hmac.compare_digest(decoded, expected)

        def _send_unauthorized(self) -> None:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="Switch Catalog"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send_directory_listing(self, *, head_only: bool) -> None:
            rows = _catalog_files(db_path)
            links = "".join(
                f'<a href="{quote(row.file_name)}">{html.escape(row.file_name)}</a>\n'
                for row in sorted(rows, key=lambda item: item.file_name.lower())
            )
            body = (
                "<html><head><title>Index of /dir/</title></head><body>\n"
                f"<h1>Index of /dir/</h1>\n{links}</body></html>"
            ).encode("utf-8")
            self._send_payload(body, "text/html; charset=utf-8", head_only=head_only)

        def _send_url_list(self, *, head_only: bool) -> None:
            base_url = self._request_base_url()
            lines = [
                f"{base_url}/dl/{row.kind}/{row.id}/{quote(row.file_name)}"
                for row in _catalog_files(db_path)
            ]
            self._send_payload(("\n".join(lines) + "\n").encode("utf-8"), "text/plain; charset=utf-8", head_only=head_only)

        def _request_base_url(self) -> str:
            host = self.headers.get("Host") or f"{self.server.server_name}:{self.server.server_port}"
            if password:
                user = quote(username, safe="")
                pwd = quote(password, safe="")
                host = f"{user}:{pwd}@{host}"
            return f"http://{host}"

        def _send_download_by_id(self, kind: str, file_id: int, *, head_only: bool) -> None:
            record = _catalog_file_by_id(db_path, kind, file_id)
            if record is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(record, head_only=head_only)

        def _send_download_by_name(self, encoded_name: str, *, head_only: bool) -> None:
            name = unquote(encoded_name)
            if not name or "/" in name or "\\" in name:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            record = _catalog_file_by_name(db_path, name)
            if record is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(record, head_only=head_only)

        def _send_file(self, record: CatalogFile, *, head_only: bool) -> None:
            path = Path(record.file_path)
            if not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            size = path.stat().st_size
            range_header = self.headers.get("Range")
            byte_range = _parse_range(range_header, size) if range_header else (0, size - 1)
            if byte_range is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = byte_range
            content_length = end - start + 1
            status = HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK
            content_type = mimetypes.guess_type(record.file_name)[0] or "application/octet-stream"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))
            self.send_header("Content-Disposition", _content_disposition(record.file_name))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return
            with path.open("rb") as file:
                file.seek(start)
                remaining = content_length
                while remaining:
                    chunk = file.read(min(_CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        break

        def _send_payload(self, body: bytes, content_type: str, *, head_only: bool) -> None:
            size = len(body)
            start, end = 0, size - 1
            status = HTTPStatus.OK
            range_header = self.headers.get("Range")
            if range_header:
                parsed = _parse_range(range_header, size)
                if parsed is None:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = parsed
                status = HTTPStatus.PARTIAL_CONTENT
            chunk = body[start : end + 1]
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Accept-Ranges", "bytes")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    return CatalogRequestHandler


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _catalog_files(db_path: Path) -> list[CatalogFile]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT 'game' AS kind, gf.id, gf.file_path, gf.file_name, gf.file_size, gf.modified_time
            FROM game_files gf
            WHERE gf.is_base_game=1
            UNION ALL
            SELECT 'update' AS kind, u.id, u.file_path, u.file_name, u.file_size, u.modified_time
            FROM updates u
            ORDER BY file_name COLLATE NOCASE
            """
        ).fetchall()
    return [_row_to_catalog_file(row) for row in rows]


def _catalog_file_by_id(db_path: Path, kind: str, file_id: int) -> CatalogFile | None:
    with _connect(db_path) as conn:
        if kind == "game":
            row = conn.execute(
                """
                SELECT 'game' AS kind, id, file_path, file_name, file_size, modified_time
                FROM game_files
                WHERE id=? AND is_base_game=1
                """,
                (file_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 'update' AS kind, id, file_path, file_name, file_size, modified_time
                FROM updates
                WHERE id=?
                """,
                (file_id,),
            ).fetchone()
    return _row_to_catalog_file(row) if row else None


def _catalog_file_by_name(db_path: Path, file_name: str) -> CatalogFile | None:
    matches = [row for row in _catalog_files(db_path) if os.path.basename(row.file_name) == file_name]
    if not matches:
        return None
    return matches[0]


def _row_to_catalog_file(row: sqlite3.Row) -> CatalogFile:
    return CatalogFile(
        kind=str(row["kind"]),
        id=int(row["id"]),
        file_path=str(row["file_path"]),
        file_name=str(row["file_name"]),
        file_size=int(row["file_size"]),
        modified_time=float(row["modified_time"]),
    )


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if size < 1 or not value:
        return None
    match = re.match(r"bytes=(\d*)-(\d*)$", value.strip())
    if not match:
        return None
    start_text, end_text = match.group(1), match.group(2)
    if start_text == "" and end_text == "":
        return None
    if start_text == "":
        suffix_length = int(end_text)
        if suffix_length == 0:
            return None
        return max(0, size - suffix_length), size - 1
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


def _content_disposition(file_name: str) -> str:
    ascii_name = file_name.encode("ascii", "replace").decode("ascii").replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
