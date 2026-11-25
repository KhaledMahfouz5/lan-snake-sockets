"""Central configuration constants for the LAN Snake server."""

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
SOCKET_TIMEOUT_SECONDS = 5.0
MAX_HEADER_BYTES = 16384
MAX_BODY_BYTES = 65536

GRID_WIDTH = 30
GRID_HEIGHT = 20
TICK_MS = 150
TICK_SECONDS = TICK_MS / 1000.0

MIN_PLAYERS = 1
MAX_PLAYERS = 4
TOKEN_HEX_BYTES = 16  # 16 random bytes -> 32 hex chars

VALID_KEYS = ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight")

# Key-ownership table keyed by room player count N (1..4).
# For N == 1, actions are absolute directions ("UP", "DOWN", "LEFT", "RIGHT").
# For N >= 2, actions are relative turns ("CCW", "CW") from current heading.
KEY_OWNERSHIP = {
    1: {
        1: {
            "ArrowUp": "UP",
            "ArrowDown": "DOWN",
            "ArrowLeft": "LEFT",
            "ArrowRight": "RIGHT",
        },
    },
    2: {
        1: {
            "ArrowUp": "CCW",
            "ArrowDown": "CW",
        },
        2: {
            "ArrowLeft": "CCW",
            "ArrowRight": "CW",
        },
    },
    3: {
        1: {
            "ArrowLeft": "CCW",
            "ArrowRight": "CW",
        },
        2: {
            "ArrowUp": "CW",
        },
        3: {
            "ArrowDown": "CW",
        },
    },
    4: {
        1: {
            "ArrowUp": "CW",
        },
        2: {
            "ArrowDown": "CW",
        },
        3: {
            "ArrowLeft": "CW",
        },
        4: {
            "ArrowRight": "CW",
        },
    },
}