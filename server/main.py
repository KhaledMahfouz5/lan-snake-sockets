"""Entry point for the LAN Snake raw-socket HTTP server and game loop."""

import argparse
from datetime import datetime
import socket
import sys
import threading
import time

from server import api, config, game, http_layer


def detect_lan_ip() -> str:
    """Best-effort discovery of the host's primary LAN IPv4 address."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.settimeout(1.0)
        # No packets need to be sent for UDP connect to select the outbound interface.
        probe.connect(("8.8.8.8", 80))
        ip_addr = probe.getsockname()[0]
        if ip_addr and not ip_addr.startswith("127."):
            return ip_addr
    except OSError:
        pass
    finally:
        probe.close()

    try:
        hostname_ip = socket.gethostbyname(socket.gethostname())
        if hostname_ip and not hostname_ip.startswith("127."):
            return hostname_ip
    except OSError:
        pass

    return "127.0.0.1"


def _log_request(client_addr: tuple[str, int], method: str, path: str, status_code: int) -> None:
    """Print one concise timestamped request log line."""
    now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    client_str = f"{client_addr[0]}:{client_addr[1]}"
    print(f"[{now_str}] {client_str} {method} {path} -> {status_code}", flush=True)


def handle_client(conn: socket.socket, client_addr: tuple[str, int]) -> None:
    """Handle a single client TCP connection from request parse to response & close."""
    method = "-"
    path = "-"
    status_code = 400

    try:
        conn.settimeout(config.SOCKET_TIMEOUT_SECONDS)
        req = http_layer.receive_request(conn)
        method = req.method
        path = req.path
        status_code, raw_response = api.route_request(req)
    except http_layer.HttpProtocolError as exc:
        status_code = exc.status_code
        raw_response = http_layer.build_json_response(status_code, {"error": exc.message})
    except socket.timeout:
        status_code = 400
        raw_response = http_layer.build_json_response(400, {"error": "Socket read timeout."})
    except OSError:
        status_code = 400
        raw_response = http_layer.build_json_response(400, {"error": "Socket error."})
    finally:
        pass

    http_layer.send_all_and_close(conn, raw_response)
    _log_request(client_addr, method, path, status_code)


def game_loop_worker(stop_event: threading.Event) -> None:
    """Advance game ticks at a fixed interval until stop_event is set."""
    interval = config.TICK_SECONDS
    next_deadline = time.monotonic() + interval

    while not stop_event.is_set():
        now = time.monotonic()
        sleep_for = next_deadline - now
        if sleep_for > 0:
            stop_event.wait(timeout=sleep_for)
            if stop_event.is_set():
                break

        try:
            game.step_tick()
        except Exception as exc:
            print(f"[ERROR] Game loop tick failed: {exc}", file=sys.stderr, flush=True)

        next_deadline += interval
        # Avoid catch-up bursts if the thread was delayed significantly.
        if next_deadline < time.monotonic():
            next_deadline = time.monotonic() + interval


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI options for the server."""
    parser = argparse.ArgumentParser(description="LAN Multiplayer Snake Socket Server")
    parser.add_argument(
        "--port",
        type=int,
        default=config.DEFAULT_PORT,
        help=f"TCP port to listen on (default: {config.DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=config.DEFAULT_HOST,
        help=f"Bind address (default: {config.DEFAULT_HOST})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Start the game-loop thread and raw TCP accept loop."""
    args = parse_args(argv)
    lan_ip = detect_lan_ip()
    stop_event = threading.Event()

    ticker_thread = threading.Thread(
        target=game_loop_worker,
        args=(stop_event,),
        name="game-loop",
        daemon=True,
    )
    ticker_thread.start()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_sock.bind((args.host, args.port))
        server_sock.listen(128)
        server_sock.settimeout(1.0)
    except OSError as exc:
        print(f"[ERROR] Failed to bind {args.host}:{args.port}: {exc}", file=sys.stderr, flush=True)
        stop_event.set()
        server_sock.close()
        return 1

    print(f"Server listening on {args.host}:{args.port}", flush=True)
    print(f"LAN IP detected: {lan_ip}", flush=True)
    print(f"Open in browser: http://{lan_ip}:{args.port}/", flush=True)

    try:
        while True:
            try:
                conn, client_addr = server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            worker = threading.Thread(
                target=handle_client,
                args=(conn, client_addr),
                name=f"conn-{client_addr[0]}:{client_addr[1]}",
                daemon=True,
            )
            worker.start()
    except KeyboardInterrupt:
        print("\nShutting down server...", flush=True)
    finally:
        stop_event.set()
        try:
            server_sock.close()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())