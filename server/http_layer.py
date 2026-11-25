"""Hand-written HTTP/1.1 request parser and response serializer over raw sockets."""

from dataclasses import dataclass
import json
import socket
from typing import Any
from urllib.parse import parse_qs, urlsplit

from server import config

REASON_PHRASES = {
    200: "OK",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    500: "Internal Server Error",
}


class HttpProtocolError(Exception):
    """Raised when incoming bytes violate HTTP framing or syntax rules."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class HttpRequest:
    method: str
    raw_target: str
    path: str
    query: dict[str, list[str]]
    version: str
    headers: dict[str, str]
    body: bytes


def _recv_until_header_end(conn: socket.socket) -> tuple[bytes, bytes]:
    """Read from socket until '\\r\\n\\r\\n', returning (header_bytes, leftover_body)."""
    buffer = bytearray()
    delimiter = b"\r\n\r\n"

    while delimiter not in buffer:
        chunk = conn.recv(4096)
        if not chunk:
            raise HttpProtocolError(400, "Client closed connection before end of headers.")
        buffer.extend(chunk)
        if len(buffer) > config.MAX_HEADER_BYTES:
            raise HttpProtocolError(413, "HTTP headers exceed maximum allowed size.")

    split_idx = buffer.index(delimiter)
    header_bytes = bytes(buffer[:split_idx])
    leftover = bytes(buffer[split_idx + len(delimiter) :])
    return header_bytes, leftover


def _parse_headers(header_lines: list[str]) -> dict[str, str]:
    """Parse header lines into a lowercase-keyed dictionary."""
    headers: dict[str, str] = {}
    for line in header_lines:
        if not line or ":" not in line:
            raise HttpProtocolError(400, "Malformed HTTP header line.")
        name, value = line.split(":", 1)
        clean_name = name.strip().lower()
        clean_value = value.strip()
        if not clean_name or any(ch.isspace() for ch in clean_name):
            raise HttpProtocolError(400, "Invalid HTTP header name.")
        headers[clean_name] = clean_value
    return headers


def receive_request(conn: socket.socket) -> HttpRequest:
    """Read and parse one complete HTTP/1.1 request from a connected socket."""
    header_bytes, body_buf = _recv_until_header_end(conn)

    try:
        header_text = header_bytes.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise HttpProtocolError(400, "Invalid header encoding.") from exc

    lines = header_text.split("\r\n")
    if not lines or not lines[0]:
        raise HttpProtocolError(400, "Empty HTTP request line.")

    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise HttpProtocolError(400, "Malformed HTTP request line.")

    method, raw_target, version = parts[0].upper(), parts[1], parts[2].upper()
    if not method or not raw_target.startswith("/") or not version.startswith("HTTP/"):
        raise HttpProtocolError(400, "Invalid method, target, or HTTP version.")

    headers = _parse_headers(lines[1:])

    if "transfer-encoding" in headers:
        raise HttpProtocolError(400, "Transfer-Encoding is not supported.")

    content_length = 0
    if "content-length" in headers:
        raw_len = headers["content-length"]
        if not raw_len.isdigit():
            raise HttpProtocolError(400, "Invalid Content-Length header.")
        content_length = int(raw_len)
        if content_length < 0:
            raise HttpProtocolError(400, "Negative Content-Length.")
        if content_length > config.MAX_BODY_BYTES:
            raise HttpProtocolError(413, "Request body exceeds maximum allowed size.")

    body_bytes = bytearray(body_buf)
    if len(body_bytes) > content_length:
        body_bytes = body_bytes[:content_length]

    while len(body_bytes) < content_length:
        to_read = min(4096, content_length - len(body_bytes))
        chunk = conn.recv(to_read)
        if not chunk:
            raise HttpProtocolError(400, "Unexpected EOF while reading request body.")
        body_bytes.extend(chunk)

    split_target = urlsplit(raw_target)
    path = split_target.path or "/"
    query = parse_qs(split_target.query, keep_blank_values=True)

    return HttpRequest(
        method=method,
        raw_target=raw_target,
        path=path,
        query=query,
        version=version,
        headers=headers,
        body=bytes(body_bytes),
    )


def parse_json_body(request: HttpRequest) -> dict[str, Any]:
    """Decode request body as UTF-8 JSON object or raise HttpProtocolError(400)."""
    if not request.body:
        raise HttpProtocolError(400, "Request body must not be empty.")
    try:
        decoded = request.body.decode("utf-8")
        data = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpProtocolError(400, "Invalid JSON payload.") from exc

    if not isinstance(data, dict):
        raise HttpProtocolError(400, "JSON root must be an object.")
    return data


def build_response(
    status_code: int,
    body: bytes | str,
    content_type: str = "application/json; charset=utf-8",
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Serialize a complete HTTP/1.1 response with Connection: close."""
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    reason = REASON_PHRASES.get(status_code, "Status")

    headers_map: dict[str, str] = {
        "Content-Type": content_type,
        "Content-Length": str(len(body_bytes)),
        "Connection": "close",
    }
    if extra_headers:
        for key, value in extra_headers.items():
            headers_map[key] = value

    response_lines = [f"HTTP/1.1 {status_code} {reason}"]
    for key, value in headers_map.items():
        response_lines.append(f"{key}: {value}")

    header_block = ("\r\n".join(response_lines) + "\r\n\r\n").encode("iso-8859-1")
    return header_block + body_bytes


def build_json_response(
    status_code: int,
    payload: dict[str, Any],
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Serialize a JSON dictionary response with no-store caching."""
    merged_headers = {"Cache-Control": "no-store"}
    if extra_headers:
        merged_headers.update(extra_headers)
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return build_response(
        status_code=status_code,
        body=encoded,
        content_type="application/json; charset=utf-8",
        extra_headers=merged_headers,
    )


def send_all_and_close(conn: socket.socket, raw_response: bytes) -> None:
    """Send full HTTP response bytes over the socket and close it cleanly."""
    try:
        conn.sendall(raw_response)
    except OSError:
        pass
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass