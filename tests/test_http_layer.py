"""Unit tests for server.http_layer framing, parsing across split recv chunks, and errors."""

import io
import socket
import unittest

from server import http_layer


class FakeSocket:
    """Simulated socket feeding pre-chunked byte slices across recv() calls."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent = bytearray()
        self.closed = False

    def recv(self, _bufsize: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def shutdown(self, _how: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class HttpLayerTests(unittest.TestCase):
    def test_post_request_split_across_multiple_recv_calls(self) -> None:
        body_str = '{"token":"abc123","key":"ArrowUp"}'
        raw_req = (
            "POST /api/input HTTP/1.1\r\n"
            "Host: 127.0.0.1:8080\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body_str)}\r\n"
            "\r\n"
            f"{body_str}"
        ).encode("utf-8")

        # Split right inside the JSON body across multiple recv() calls.
        split_point = raw_req.index(b'{"token"') + 9
        chunks = [
            raw_req[:25],
            raw_req[25:split_point],
            raw_req[split_point : split_point + 8],
            raw_req[split_point + 8 :],
        ]
        fake_sock = FakeSocket(chunks)

        req = http_layer.receive_request(fake_sock)  # type: ignore[arg-type]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.path, "/api/input")
        parsed = http_layer.parse_json_body(req)
        self.assertEqual(parsed, {"token": "abc123", "key": "ArrowUp"})

    def test_malformed_request_line_raises_400(self) -> None:
        fake_sock = FakeSocket([b"BADREQUEST\r\nHost: localhost\r\n\r\n"])
        with self.assertRaises(http_layer.HttpProtocolError) as ctx:
            http_layer.receive_request(fake_sock)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.status_code, 400)

    def test_invalid_content_length_raises_400(self) -> None:
        fake_sock = FakeSocket(
            [b"POST /api/join HTTP/1.1\r\nHost: localhost\r\nContent-Length: abc\r\n\r\n"]
        )
        with self.assertRaises(http_layer.HttpProtocolError) as ctx:
            http_layer.receive_request(fake_sock)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.status_code, 400)

    def test_build_json_response_includes_required_headers(self) -> None:
        raw = http_layer.build_json_response(200, {"ok": True})
        header_part, body_part = raw.split(b"\r\n\r\n", 1)
        headers = header_part.decode("iso-8859-1").split("\r\n")
        self.assertEqual(headers[0], "HTTP/1.1 200 OK")
        self.assertIn("Content-Type: application/json; charset=utf-8", headers)
        self.assertIn("Content-Length: 10", headers)
        self.assertIn("Cache-Control: no-store", headers)
        self.assertIn("Connection: close", headers)
        self.assertEqual(body_part, b'{"ok":true}')

    def test_socket_pair_roundtrip(self) -> None:
        s_client, s_server = socket.socketpair()
        try:
            payload = b'{"name":"Tester","num_players":1}'
            wire = (
                b"POST /api/join HTTP/1.1\r\n"
                b"Host: localhost:8080\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
                + payload
            )
            s_client.sendall(wire)
            s_client.shutdown(socket.SHUT_WR)

            req = http_layer.receive_request(s_server)
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.path, "/api/join")
            self.assertEqual(http_layer.parse_json_body(req)["name"], "Tester")

            resp = http_layer.build_json_response(200, {"player_id": "p1-test"})
            http_layer.send_all_and_close(s_server, resp)

            buf = io.BytesIO()
            while True:
                data = s_client.recv(1024)
                if not data:
                    break
                buf.write(data)
            self.assertIn(b"HTTP/1.1 200 OK", buf.getvalue())
        finally:
            s_client.close()
            try:
                s_server.close()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()