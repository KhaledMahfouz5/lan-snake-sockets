# LAN Snake HTTP / JSON API Contract

All API endpoints exchange JSON (`Content-Type: application/json; charset=utf-8`), use same-origin requests over plain HTTP/1.1 (`Connection: close`), and include `Cache-Control: no-store` on API responses.

## Endpoints

### 1. `POST /api/join`

Joins the global room. The first player sets `num_players` (`1` to `4`). When `num_players` players have joined, the game transitions immediately to `running` (so `N=1` starts right away).

- **Example Request**

```http
POST /api/join HTTP/1.1
Host: 192.168.1.50:8080
Content-Type: application/json
Content-Length: 41

{"name":"PlayerOne","num_players":2}
```

- **Example Response (`200 OK`)**

```json
{"player_id":"p1-9a4f12","token":"4f8c1d9e2b706a315c8e0f1294a7b6c5","slot":1,"owned_keys":["ArrowDown","ArrowUp"],"num_players":2}
```

### 2. `GET /api/state?token=...`

Returns the current room snapshot (`lobby`, `running`, or `finished`). `token` is optional for read-only inspection; when supplied, it is validated (`401` if invalid/unknown) and populates `you`.

- **Example Request**

```http
GET /api/state?token=4f8c1d9e2b706a315c8e0f1294a7b6c5 HTTP/1.1
Host: 192.168.1.50:8080
```

- **Example Response (`200 OK`)**

```json
{"phase":"running","tick":14,"grid":{"w":30,"h":20},"players":[{"id":"p1-9a4f12","name":"PlayerOne","alive":true,"score":1},{"id":"p2-3c810e","name":"PlayerTwo","alive":true,"score":0}],"snakes":[{"player_id":"p1-9a4f12","cells":[[9,10],[8,10],[7,10],[6,10]],"alive":true},{"player_id":"p2-3c810e","cells":[[20,10],[21,10],[22,10]],"alive":true}],"food":[[15,4],[12,17]],"winner":null,"you":{"player_id":"p1-9a4f12","owned_keys":["ArrowDown","ArrowUp"]},"joined":2,"needed":2}
```

### 3. `POST /api/input`

Submits an arrow key press for the authenticated player. Validated inputs take effect on the next server tick; if multiple valid inputs arrive during the same tick, the last one wins.

- **Example Request**

```http
POST /api/input HTTP/1.1
Host: 192.168.1.50:8080
Content-Type: application/json
Content-Length: 62

{"token":"4f8c1d9e2b706a315c8e0f1294a7b6c5","key":"ArrowUp"}
```

- **Example Response (`200 OK`)**

```json
{"ok":true}
```

- **Example Error Response (`403 Forbidden` when key is not owned by this player)**

```json
{"error":"Key 'ArrowRight' is not assigned to player slot 1."}
```

### 4. `POST /api/reset`

Returns a `finished` room back to `lobby` and clears active players, snakes, and food.

- **Example Request**

```http
POST /api/reset HTTP/1.1
Host: 192.168.1.50:8080
Content-Type: application/json
Content-Length: 45

{"token":"4f8c1d9e2b706a315c8e0f1294a7b6c5"}
```

- **Example Response (`200 OK`)**

```json
{"ok":true,"phase":"lobby"}
```

### 5. `GET /` and `GET /static/<file>`

Serves `web/index.html` at `/` (and `/index.html`) and files under `web/` at `/static/...` (such as `/static/style.css` and `/static/app.js`).

- **Example Request**

```http
GET /static/app.js HTTP/1.1
Host: 192.168.1.50:8080
```

- **Example Response (`200 OK`)**

```http
HTTP/1.1 200 OK
Content-Type: application/javascript; charset=utf-8
Content-Length: 8420
Connection: close

"use strict";
...
```

## Error Codes

- `400 Bad Request`: Malformed HTTP framing, invalid JSON, or invalid field types/values.
- `401 Unauthorized`: Missing or unrecognized `token` on protected endpoints or invalid query `token`.
- `403 Forbidden`: Valid `token`, but the player does not own the requested arrow key (or path traversal attempt on static path).
- `404 Not Found`: Unknown route or missing static file.
- `405 Method Not Allowed`: HTTP method not allowed for the route.
- `409 Conflict`: Room full, game already running/finished on join, input sent when game not running or player eliminated, or reset called before game finished.
- `413 Payload Too Large`: Headers or body exceed configured byte limits.
- `500 Internal Server Error`: Unexpected unhandled server error.