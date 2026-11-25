# LAN Multiplayer Snake (Raw Python Sockets)

A LAN multiplayer Snake game (1 to 4 players) with a browser frontend (`HTML`/`CSS`/`JavaScript` on `<canvas>`) and a backend written from scratch in Python 3.10+ using the raw `socket` module, `threading`, and a single `threading.Lock`.

Authors: <STUDENT_1>, <STUDENT_2>. Course: <COURSE_NAME>. Instructor: <INSTRUCTOR_NAME>.

## Architecture Overview

- `server/config.py`: Central constants for grid size (`30x20`), tick rate (`150 ms`), port (`8080`), token length, and the arrow-ownership table (`1` to `4` players).
- `server/game.py`: Pure game engine protected by one `threading.Lock`. Handles room lifecycle (`lobby`, `running`, `finished`), fair arrow assignment, relative vs. absolute turns, food spawning, collisions, scoring, and tick updates. Does not import `socket`.
- `server/http_layer.py`: Hand-rolled HTTP/1.1 reader and serializer over raw `socket` objects (`recv` loop until `\r\n\r\n`, `Content-Length` framing, status codes `200`/`400`/`401`/`403`/`404`/`405`/`409`/`413`/`500`, `Connection: close`). Does not import `game`.
- `server/api.py`: Routes endpoints, validates JSON payloads and query tokens, serves static files under `web/` safely (preventing path traversal), and translates `GameError` into HTTP JSON responses.
- `server/main.py`: CLI entry point (`--port`, `--host`), prints the LAN IP and URL, spawns a daemon game-loop thread, and spawns one daemon thread per incoming TCP connection with concise per-request logging.
- `web/`: Vanilla HTML/CSS/JavaScript Nokia-style monochrome green LCD interface using `<canvas>` and non-overlapping polling (`120 ms`) with `AbortController` timeouts.
- `tests/`: Standard-library `unittest` suites covering game rules (`tests/test_game.py`) and HTTP framing/parsing (`tests/test_http_layer.py`).
- `docs/`: API contract (`docs/API.md`) and Wireshark analysis guide (`docs/WIRESHARK.md`).

## How to Run

1. Open a terminal in the repository root (`Python 3.10+`, standard library only).
2. Start the server:

```bash
python3 -m server.main
```

Optional flags:

```bash
python3 -m server.main --port 8080 --host 0.0.0.0
```

3. Open the printed URL (`http://<server-ip>:8080/`) in **1 to 4 browsers or devices** connected to the same local network (LAN).
4. **Windows Firewall note for port 8080**: When running on Windows for the first time, Windows Defender Firewall may prompt to allow `python.exe` on Private/LAN networks. Click **Allow access**, or run an elevated PowerShell command to allow inbound TCP port `8080` on private networks:

```powershell
New-NetFirewallRule -DisplayName "LAN Snake Lab Port 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Profile Private -Action Allow
```

5. Run unit tests:

```bash
python -m unittest discover tests
```

## Documentation

- API reference: [docs/API.md](docs/API.md)
- Wireshark capture & packet analysis guide: [docs/WIRESHARK.md](docs/WIRESHARK.md)
