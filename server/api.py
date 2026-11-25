"""HTTP routing, payload validation, and static file serving over game logic."""

import json
from pathlib import Path
from typing import Any

from server import game
from server.http_layer import HttpProtocolError, HttpRequest, build_json_response, build_response, parse_json_body

WEB_ROOT = (Path(__file__).resolve().parent.parent / "web").resolve()

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _serve_static_file(rel_path: str) -> bytes:
    """Safely serve a file inside WEB_ROOT without allowing path traversal."""
    cleaned = rel_path.lstrip("/")
    if not cleaned or cleaned == "index.html":
        target = (WEB_ROOT / "index.html").resolve()
    else:
        target = (WEB_ROOT / cleaned).resolve()

    # Ensure resolved path stays strictly inside WEB_ROOT.
    if target != WEB_ROOT and WEB_ROOT not in target.parents:
        return build_json_response(403, {"error": "Forbidden path."})

    if not target.exists() or not target.is_file():
        return build_json_response(404, {"error": "Static resource not found."})

    suffix = target.suffix.lower()
    content_type = MIME_TYPES.get(suffix, "application/octet-stream")
    data = target.read_bytes()
    return build_response(200, data, content_type=content_type)


def _extract_token_from_query(request: HttpRequest) -> str | None:
    """Return query parameter 'token' if present."""
    values = request.query.get("token")
    if not values:
        return None
    return values[0]


def _handle_join(request: HttpRequest) -> bytes:
    """Handle POST /api/join."""
    data = parse_json_body(request)
    raw_name = data.get("name", "")
    if not isinstance(raw_name, str):
        raise HttpProtocolError(400, "'name' must be a string.")

    num_players: int | None = None
    if "num_players" in data and data["num_players"] is not None:
        val = data["num_players"]
        if not isinstance(val, int) or isinstance(val, bool):
            raise HttpProtocolError(400, "'num_players' must be an integer.")
        num_players = val

    result: dict[str, Any] = game.join_player(name=raw_name, num_players=num_players)
    return build_json_response(200, result)


def _handle_state(request: HttpRequest) -> bytes:
    """Handle GET /api/state?token=..."""
    token = _extract_token_from_query(request)
    state = game.get_state(token=token)
    return build_json_response(200, state)


def _handle_input(request: HttpRequest) -> bytes:
    """Handle POST /api/input."""
    data = parse_json_body(request)
    token = data.get("token")
    if token is not None and not isinstance(token, str):
        raise HttpProtocolError(400, "'token' must be a string.")

    key = data.get("key")
    if not isinstance(key, str):
        raise HttpProtocolError(400, "'key' must be a string.")

    result = game.submit_input(token=token, key=key)
    return build_json_response(200, result)


def _handle_reset(request: HttpRequest) -> bytes:
    """Handle POST /api/reset."""
    data = parse_json_body(request)
    token = data.get("token")
    if token is not None and not isinstance(token, str):
        raise HttpProtocolError(400, "'token' must be a string.")

    result = game.reset_room(token=token)
    return build_json_response(200, result)


def route_request(request: HttpRequest) -> tuple[int, bytes]:
    """Dispatch an HttpRequest to static or API handlers and return (status_code, raw_bytes)."""
    path = request.path
    method = request.method

    try:
        if path == "/" or path == "/index.html":
            if method != "GET":
                resp = build_json_response(405, {"error": "Method not allowed."})
                return 405, resp
            resp = _serve_static_file("index.html")
            return 200, resp

        if path.startswith("/static/"):
            if method != "GET":
                resp = build_json_response(405, {"error": "Method not allowed."})
                return 405, resp
            rel = path[len("/static/") :]
            resp = _serve_static_file(rel)
            status = 200 if b"HTTP/1.1 200 " in resp[:20] else 404
            return status, resp

        if path == "/api/join":
            if method != "POST":
                return 405, build_json_response(405, {"error": "Method not allowed."})
            return 200, _handle_join(request)

        if path == "/api/state":
            if method != "GET":
                return 405, build_json_response(405, {"error": "Method not allowed."})
            return 200, _handle_state(request)

        if path == "/api/input":
            if method != "POST":
                return 405, build_json_response(405, {"error": "Method not allowed."})
            return 200, _handle_input(request)

        if path == "/api/reset":
            if method != "POST":
                return 405, build_json_response(405, {"error": "Method not allowed."})
            return 200, _handle_reset(request)

        return 404, build_json_response(404, {"error": "Not found."})

    except HttpProtocolError as exc:
        return exc.status_code, build_json_response(exc.status_code, {"error": exc.message})
    except game.GameError as exc:
        return exc.status_code, build_json_response(exc.status_code, {"error": exc.message})
    except json.JSONDecodeError:
        return 400, build_json_response(400, {"error": "Malformed JSON."})
    except Exception:
        return 500, build_json_response(500, {"error": "Internal server error."})