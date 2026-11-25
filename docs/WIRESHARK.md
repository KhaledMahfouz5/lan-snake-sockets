# Wireshark Capture and Packet Analysis Guide

This project runs over unencrypted HTTP/1.1 on TCP port `8080` (`Connection: close` per request), without TLS or HTTP compression, so headers, JSON payloads, and TCP control flags are directly inspectable in Wireshark.

## 1. Selecting the Capture Interface for LAN Traffic

- **Cross-machine LAN traffic** (client browser on Device B connecting to server on Device A): Select the active physical Ethernet or Wi-Fi adapter on the capturing machine (not `Loopback` and not an unused VM/VPN interface).
- **Same-machine testing** (`127.0.0.1` / `localhost`): Select the **Loopback** interface (`lo` on Linux, `Adapter for loopback traffic capture` / `Npcap Loopback Adapter` on Windows).

## 2. Capture and Display Filters

- **Capture filter** (set before starting capture to keep `.pcapng` focused on port `8080`):
  ```text
  tcp port 8080
  ```
- **Display filters** (apply in the Wireshark filter bar after or during capture):
  - All HTTP traffic on the filtered capture:
    ```text
    http
    ```
  - Only `POST` requests (`POST /api/join`, `POST /api/input`, `POST /api/reset`):
    ```text
    http.request.method == "POST"
    ```
  - Traffic on port `8080` involving one specific client IP (replace `<client>` with the client IPv4 address):
    ```text
    tcp.port == 8080 && ip.addr == <client>
    ```
  - Packets containing JSON bodies/headers:
    ```text
    json
    ```

## 3. Following a TCP Stream

1. Click any HTTP packet belonging to a request/response exchange in the packet list.
2. Right-click and choose **Follow -> TCP Stream** (or press `Ctrl+Alt+Shift+T` / `Cmd+Alt+Shift+T`).
3. Inspect the client request (request line, headers, JSON body) in red and the server response (`HTTP/1.1 ...`, `Content-Type`, `Content-Length`, `Cache-Control: no-store`, `Connection: close`, JSON body) in blue.
4. Close the stream window to return to the filtered packet view.

## 4. Reading the TCP 3-Way Handshake and FIN Teardown

Because the server uses `Connection: close` for each HTTP request/response cycle:

1. **3-Way Handshake** (client ephemeral port `C` -> server port `8080`):
   - `SYN` (`Client -> Server`, `Seq=0` relative): Client initiates connection.
   - `SYN, ACK` (`Server -> Client`, `Ack=1`): Server acknowledges and offers its sequence number.
   - `ACK` (`Client -> Server`, `Ack=1`): Handshake completes; HTTP request bytes follow.
2. **HTTP Request & Response**:
   - Client sends `POST /api/input` (or `GET /api/state`) and `ACK`s data segments.
   - Server responds with status line, headers, `Content-Length` body, and `Connection: close`.
3. **FIN Connection Close**:
   - Server calls `shutdown` / `close` after sending the response, emitting `FIN, ACK` (`Server -> Client`).
   - Client acknowledges and sends its own `FIN, ACK`, followed by final `ACK`, closing that TCP connection before the next request opens a new TCP connection.

## 5. Observing Interleaved Requests from Multiple Clients

1. Run a 2-player or 4-player session where two or more browsers poll `GET /api/state` and send `POST /api/input` concurrently.
2. Filter with `tcp port 8080` (or `http`).
3. Sort by **No.** (packet arrival order) and compare source/destination ports (`client_1_ephemeral_port` vs. `client_2_ephemeral_port` to `8080`).
4. Notice how TCP packets from different client source ports interleave on the wire while the server's per-connection threads handle each socket concurrently and `game.py` serializes state mutations under `_room_lock`.

## 6. Screenshot Checklist for Lab Submission

- [ ] **Screenshot 1 — TCP 3-Way Handshake & FIN**: Packet list showing `SYN`, `SYN, ACK`, `ACK`, HTTP exchange, and `FIN, ACK` teardown on `tcp port 8080`.
- [ ] **Screenshot 2 — `POST /api/join` or `POST /api/input` Request & `200 OK` Response**: Expanded HTTP + JSON dissector view showing request headers, JSON body, and response headers (`Content-Type`, `Content-Length`, `Cache-Control: no-store`, `Connection: close`).
- [ ] **Screenshot 3 — `403 Forbidden` Arrow-Ownership Rejection**: HTTP response showing status `403 Forbidden` and JSON `{"error": ...}` when a player sends an unowned arrow key.
- [ ] **Screenshot 4 — `GET /api/state` Polling & Response**: HTTP `GET /api/state?token=...` request and JSON game state (`phase`, `tick`, `snakes`, `food`, `players`).
- [ ] **Screenshot 5 — Multi-Client Interleaving**: Packet list under `tcp port 8080` showing interleaved traffic from at least two distinct client IP addresses or ephemeral ports.
- [ ] **Screenshot 6 — Follow TCP Stream**: Stream window showing a complete single request/response cycle from start to `Connection: close`.