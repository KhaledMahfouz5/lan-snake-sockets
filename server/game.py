"""Thread-safe cooperative game engine for LAN multiplayer Snake."""

from dataclasses import dataclass, field
import random
import secrets
import threading
from typing import Any

from server import config

SHARED_SNAKE_ID = "coop-snake"


class GameError(Exception):
    """Domain error mapped to an HTTP status code by the API layer."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


OPPOSITE_DIR = {
    "UP": "DOWN",
    "DOWN": "UP",
    "LEFT": "RIGHT",
    "RIGHT": "LEFT",
}

DIR_VECTORS = {
    "UP": (0, -1),
    "DOWN": (0, 1),
    "LEFT": (-1, 0),
    "RIGHT": (1, 0),
}

CW_TURN = {
    "UP": "RIGHT",
    "RIGHT": "DOWN",
    "DOWN": "LEFT",
    "LEFT": "UP",
}

CCW_TURN = {
    "UP": "LEFT",
    "LEFT": "DOWN",
    "DOWN": "RIGHT",
    "RIGHT": "UP",
}


@dataclass
class Player:
    player_id: str
    token: str
    slot: int
    name: str
    alive: bool = True
    score: int = 0


@dataclass
class Snake:
    player_id: str
    cells: list[tuple[int, int]]
    heading: str
    alive: bool = True
    grow_pending: int = 0


@dataclass
class RoomState:
    phase: str = "lobby"  # "lobby", "running", "finished"
    num_players: int | None = None
    tick: int = 0
    players_by_token: dict[str, Player] = field(default_factory=dict)
    shared_snake: Snake | None = None
    pending_dir: str | None = None
    last_steer_player_id: str | None = None
    food: list[tuple[int, int]] = field(default_factory=list)
    winner: str | None = None


# Single lock protecting all mutable room state in this module.
# Functions acquiring _room_lock:
#   - join_player
#   - submit_input
#   - step_tick
#   - reset_room
#   - get_state
# Internal helper functions assume _room_lock is ALREADY held by the caller.
_room_lock = threading.Lock()
_room = RoomState()


def _build_shared_snake() -> Snake:
    """Create the single cooperative snake near the center facing right."""
    hx, hy = config.GRID_WIDTH // 2, config.GRID_HEIGHT // 2
    length = 3
    cells = [(hx - offset, hy) for offset in range(length) if hx - offset >= 0]
    return Snake(player_id=SHARED_SNAKE_ID, cells=cells, heading="RIGHT", alive=True)


def _occupied_cells_locked() -> set[tuple[int, int]]:
    """Return all board cells currently occupied by the shared snake or food."""
    occupied: set[tuple[int, int]] = set()
    if _room.shared_snake is not None and _room.shared_snake.alive:
        occupied.update(_room.shared_snake.cells)
    occupied.update(_room.food)
    return occupied


def _spawn_food_locked(target_count: int) -> None:
    """Spawn food items up to target_count on unoccupied cells."""
    if len(_room.food) >= target_count:
        return
    occupied = _occupied_cells_locked()
    free_cells = [
        (x, y)
        for y in range(config.GRID_HEIGHT)
        for x in range(config.GRID_WIDTH)
        if (x, y) not in occupied
    ]
    random.shuffle(free_cells)
    while len(_room.food) < target_count and free_cells:
        _room.food.append(free_cells.pop())


def _start_game_locked() -> None:
    """Transition the room from lobby to running once all N players join."""
    assert _room.num_players is not None
    _room.phase = "running"
    _room.tick = 0
    _room.winner = None
    _room.food = []
    _room.pending_dir = None
    _room.shared_snake = _build_shared_snake()

    ordered_players = sorted(_room.players_by_token.values(), key=lambda p: p.slot)
    _room.last_steer_player_id = ordered_players[0].player_id if ordered_players else None

    for player in ordered_players:
        player.alive = True
        player.score = 0

    food_target = max(1, _room.num_players)
    _spawn_food_locked(food_target)


def _resolve_desired_direction(current_heading: str, action: str, num_players: int) -> str:
    """Convert an owned key action into a candidate movement direction."""
    if num_players == 1:
        return action
    if action == "CW":
        return CW_TURN[current_heading]
    if action == "CCW":
        return CCW_TURN[current_heading]
    raise GameError(400, f"Invalid action '{action}' for {num_players} players.")


def _require_player_locked(token: str | None) -> Player:
    """Verify a session token and return the authenticated Player or raise 401."""
    if not token or not isinstance(token, str):
        raise GameError(401, "Missing or invalid token.")
    player = _room.players_by_token.get(token)
    if player is None:
        raise GameError(401, "Unknown or expired token.")
    return player


def join_player(name: str, num_players: int | None) -> dict[str, Any]:
    """Join the cooperative room and return credentials plus assigned arrow keys."""
    cleaned_name = name.strip()
    if not cleaned_name:
        cleaned_name = "Player"
    cleaned_name = cleaned_name[:24]

    with _room_lock:
        if _room.phase == "running":
            raise GameError(409, "Game is already in progress.")
        if _room.phase == "finished":
            raise GameError(409, "Game has finished; reset the room first.")

        current_count = len(_room.players_by_token)
        if current_count == 0:
            if num_players is None or not isinstance(num_players, int) or isinstance(num_players, bool):
                raise GameError(400, "First player must specify 'num_players' between 1 and 4.")
            if num_players < config.MIN_PLAYERS or num_players > config.MAX_PLAYERS:
                raise GameError(400, "'num_players' must be between 1 and 4.")
            _room.num_players = num_players
        else:
            assert _room.num_players is not None
            if current_count >= _room.num_players:
                raise GameError(409, "Room is already full.")

        assert _room.num_players is not None
        slot = current_count + 1
        player_id = f"p{slot}-{secrets.token_hex(3)}"
        token = secrets.token_hex(config.TOKEN_HEX_BYTES)
        player = Player(player_id=player_id, token=token, slot=slot, name=cleaned_name)
        _room.players_by_token[token] = player

        if len(_room.players_by_token) == _room.num_players:
            _start_game_locked()

        owned_keys = sorted(config.KEY_OWNERSHIP[_room.num_players][slot].keys())
        return {
            "player_id": player.player_id,
            "token": player.token,
            "slot": player.slot,
            "owned_keys": owned_keys,
            "num_players": _room.num_players,
        }


def submit_input(token: str | None, key: str) -> dict[str, Any]:
    """Validate token and arrow ownership, queuing direction for the shared snake."""
    if not isinstance(key, str) or key not in config.VALID_KEYS:
        raise GameError(400, "Invalid or unsupported key.")

    with _room_lock:
        player = _require_player_locked(token)
        if _room.phase != "running" or _room.num_players is None or _room.shared_snake is None:
            raise GameError(409, "Game is not currently running.")
        if not player.alive or not _room.shared_snake.alive:
            raise GameError(409, "Game has already ended.")

        slot_keys = config.KEY_OWNERSHIP[_room.num_players][player.slot]
        if key not in slot_keys:
            raise GameError(403, f"Key '{key}' is not assigned to player slot {player.slot}.")

        action = slot_keys[key]
        candidate_dir = _resolve_desired_direction(
            _room.shared_snake.heading, action, _room.num_players
        )

        # Prevent reversing directly into the snake's own neck.
        if OPPOSITE_DIR[_room.shared_snake.heading] == candidate_dir:
            return {"ok": True}

        _room.pending_dir = candidate_dir
        _room.last_steer_player_id = player.player_id
        return {"ok": True}


def step_tick() -> None:
    """Advance the cooperative shared snake by one tick if running."""
    with _room_lock:
        if _room.phase != "running" or _room.num_players is None or _room.shared_snake is None:
            return

        snake = _room.shared_snake
        if not snake.alive:
            return

        _room.tick += 1

        if _room.pending_dir is not None:
            if OPPOSITE_DIR[snake.heading] != _room.pending_dir:
                snake.heading = _room.pending_dir
            _room.pending_dir = None

        hx, hy = snake.cells[0]
        dx, dy = DIR_VECTORS[snake.heading]
        nx, ny = hx + dx, hy + dy

        # Wall collision check.
        if nx < 0 or nx >= config.GRID_WIDTH or ny < 0 or ny >= config.GRID_HEIGHT:
            snake.alive = False
            snake.cells = []
            for p in _room.players_by_token.values():
                p.alive = False
            _room.phase = "finished"
            _room.winner = None
            return

        will_eat = (nx, ny) in _room.food
        if will_eat:
            next_cells = [(nx, ny)] + list(snake.cells)
        elif snake.grow_pending > 0:
            next_cells = [(nx, ny)] + list(snake.cells)
            snake.grow_pending -= 1
        else:
            next_cells = [(nx, ny)] + list(snake.cells[:-1])

        # Self-collision check against remaining body segments.
        if (nx, ny) in next_cells[1:]:
            snake.alive = False
            snake.cells = []
            for p in _room.players_by_token.values():
                p.alive = False
            _room.phase = "finished"
            _room.winner = None
            return

        snake.cells = next_cells

        if will_eat:
            _room.food = [pt for pt in _room.food if pt != (nx, ny)]
            scorer = next(
                (p for p in _room.players_by_token.values() if p.player_id == _room.last_steer_player_id),
                None,
            )
            if scorer is not None:
                scorer.score += 1
            snake.grow_pending += 1
            _spawn_food_locked(max(1, _room.num_players))


def reset_room(token: str | None) -> dict[str, Any]:
    """Reset the cooperative room back to lobby after a finished game."""
    with _room_lock:
        _require_player_locked(token)
        if _room.phase != "finished":
            raise GameError(409, "Room can only be reset when the game is finished.")

        _room.phase = "lobby"
        _room.num_players = None
        _room.tick = 0
        _room.players_by_token.clear()
        _room.shared_snake = None
        _room.pending_dir = None
        _room.last_steer_player_id = None
        _room.food.clear()
        _room.winner = None
        return {"ok": True, "phase": "lobby"}


def get_state(token: str | None) -> dict[str, Any]:
    """Return the current game state snapshot, authenticating token if provided."""
    with _room_lock:
        you_info: dict[str, Any] | None = None
        if token is not None and token != "":
            player = _require_player_locked(token)
            owned_keys: list[str] = []
            if _room.num_players is not None:
                owned_keys = sorted(config.KEY_OWNERSHIP[_room.num_players][player.slot].keys())
            you_info = {
                "player_id": player.player_id,
                "owned_keys": owned_keys,
            }

        players_payload = [
            {
                "id": p.player_id,
                "name": p.name,
                "alive": p.alive,
                "score": p.score,
            }
            for p in sorted(_room.players_by_token.values(), key=lambda item: item.slot)
        ]

        snakes_payload: list[dict[str, Any]] = []
        if _room.shared_snake is not None and _room.shared_snake.alive:
            snakes_payload.append(
                {
                    "player_id": _room.shared_snake.player_id,
                    "cells": [[x, y] for x, y in _room.shared_snake.cells],
                    "alive": _room.shared_snake.alive,
                }
            )

        food_payload = [[x, y] for x, y in _room.food]

        return {
            "phase": _room.phase,
            "tick": _room.tick,
            "grid": {"w": config.GRID_WIDTH, "h": config.GRID_HEIGHT},
            "players": players_payload,
            "snakes": snakes_payload,
            "food": food_payload,
            "winner": _room.winner,
            "you": you_info,
            "joined": len(_room.players_by_token),
            "needed": _room.num_players,
        }