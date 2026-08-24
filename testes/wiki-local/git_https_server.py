"""Origem Smart HTTP mínima e descartável para o laboratório da wiki."""

from __future__ import annotations

import os
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_MAX_REQUEST_BYTES = 2 * 1024 * 1024
_PREFIX = "/repos"


class GitHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - contrato de BaseHTTPRequestHandler
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - contrato de BaseHTTPRequestHandler
        self._handle()

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._respond(200, b"ok\n", "text/plain")
            return
        if not parsed.path.startswith(f"{_PREFIX}/lucien.git"):
            self._respond(404, b"not found\n", "text/plain")
            return

        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._respond(400, b"invalid length\n", "text/plain")
            return
        if not 0 <= length <= _MAX_REQUEST_BYTES:
            self._respond(413, b"request too large\n", "text/plain")
            return
        body = self.rfile.read(length) if length else b""

        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "GIT_PROJECT_ROOT": "/repository",
            "GIT_HTTP_EXPORT_ALL": "1",
            "REQUEST_METHOD": self.command,
            "PATH_INFO": parsed.path[len(_PREFIX) :],
            "QUERY_STRING": parsed.query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(length),
            "REMOTE_ADDR": self.client_address[0],
            "SERVER_PROTOCOL": self.request_version,
        }
        try:
            result = subprocess.run(
                (
                    "git",
                    "-c",
                    "safe.directory=/repository/lucien.git",
                    "http-backend",
                ),
                input=body,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._respond(502, b"git backend unavailable\n", "text/plain")
            return
        if result.returncode != 0:
            self._respond(502, b"git backend failed\n", "text/plain")
            return

        separator = b"\r\n\r\n" if b"\r\n\r\n" in result.stdout else b"\n\n"
        try:
            header_block, payload = result.stdout.split(separator, 1)
        except ValueError:
            self._respond(502, b"invalid git response\n", "text/plain")
            return

        status = 200
        headers: list[tuple[str, str]] = []
        for raw_line in header_block.replace(b"\r\n", b"\n").split(b"\n"):
            name, _, value = raw_line.decode("latin-1").partition(":")
            if name.lower() == "status":
                status = int(value.strip().split(" ", 1)[0])
            elif name and value:
                headers.append((name.strip(), value.strip()))
        self.send_response(status)
        for name, value in headers:
            if name.lower() not in {"connection", "transfer-encoding", "content-length"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _respond(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        # O laboratório não registra paths ou conteúdo das requisições Git.
        return


server = ThreadingHTTPServer(("0.0.0.0", 8443), GitHandler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/certs/server.crt", "/certs/server.key")
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
