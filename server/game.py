"""Thread-safe game engine for LAN multiplayer Snake."""

from dataclasses import dataclass, field
import random
import secrets
import threading
from typing import Any

from server import config


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

# Spawn tuples: (head_x, head_y, heading, initial_length)
SPAWN_CONFIGS = {
    1: [(15, 10, "RIGHT", 3)],
    2: [(6, 10, "RIGHT", 3), (23, 10, "LEFT", 3)],
    3: [(6, 6, "RIGHT", 3), (23, 6, "LEFT", 3), (15, 16, "UP", 3)],
    4: [(6, 6, "RIGHT", 3), (23, 6, "LEFT", 3), (6, 14, "RIGHT", 3), (23, 14, "LEFT", 3)],
}


@dataclass
class Player:
    player_id: str
    token: str
    slot: int
    name: str
    alive: bool = True
    score: int = 0
    pending_dir: str | None = None


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
    snakes_by_id: dict[str, Snake] = field(default_factory=dict)
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


def _build_initial_snake(player_id: str, slot: int, num_players: int) -> Snake:
    """Create a snake facing inward from a distinct spawn position."""
    spawns = SPAWN_CONFIGS[num_players]
    hx, hy, heading, length = spawns[slot - 1]
    dx, dy = DIR_VECTORS[heading]
    cells: list[tuple[int, int]] = []
    for offset in range(length):
        cx = hx - dx * offset
        cy = hy - dy * offset
        if 0 <= cx < config.GRID_WIDTH and 0 <= cy < config.GRID_HEIGHT:
            cells.append((cx, cy))
    return Snake(player_id=player_id, cells=cells, heading=heading, alive=True)


def _occupied_cells_locked() -> set[tuple[int, int]]:
    """Return all board cells currently occupied by alive snakes or food."""
    occupied: set[tuple[int, int]] = set()
    for snake in _room.snakes_by_id.values():
        if snake.alive:
            occupied.update(snake.cells)
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
    """Transition the room from lobby to running once required players join."""
    assert _room.num_players is not None
    _room.phase = "running"
    _room.tick = 0
    _room.winner = None
    _room.food = []
    _room.snakes_by_id = {}

    for player in sorted(_room.players_by_token.values(), key=lambda p: p.slot):
        player.alive = True
        player.score = 0
        player.pending_dir = None
        _room.snakes_by_id[player.player_id] = _build_initial_snake(
            player_id=player.player_id,
            slot=player.slot,
            num_players=_room.num_players,
        )

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
    """Join the room and return player credentials and assigned keys."""
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
    """Validate token and arrow ownership, queuing a valid direction for next tick."""
    if not isinstance(key, str) or key not in config.VALID_KEYS:
        raise GameError(400, "Invalid or unsupported key.")

    with _room_lock:
        player = _require_player_locked(token)
        if _room.phase != "running" or _room.num_players is None:
            raise GameError(409, "Game is not currently running.")
        if not player.alive:
            raise GameError(409, "Eliminated players cannot send movement inputs.")

        slot_keys = config.KEY_OWNERSHIP[_room.num_players][player.slot]
        if key not in slot_keys:
            raise GameError(403, f"Key '{key}' is not assigned to player slot {player.slot}.")

        snake = _room.snakes_by_id.get(player.player_id)
        if snake is None or not snake.alive:
            raise GameError(409, "Snake is no longer active.")

        action = slot_keys[key]
        candidate_dir = _resolve_desired_direction(snake.heading, action, _room.num_players)

        # In single-player mode, ignore a 180-degree reversal into the neck.
        if _room.num_players == 1 and OPPOSITE_DIR[snake.heading] == candidate_dir:
            return {"ok": True}

        player.pending_dir = candidate_dir
        return {"ok": True}


def step_tick() -> None:
    """Advance the simulation by one tick if the game is running."""
    with _room_lock:
        if _room.phase != "running" or _room.num_players is None:
            return

        _room.tick += 1
        alive_players = [p for p in _room.players_by_token.values() if p.alive]

        # Step 1: Apply queued inputs and compute proposed next head positions.
        proposed_heads: dict[str, tuple[int, int]] = {}
        for player in alive_players:
            snake = _room.snakes_by_id[player.player_id]
            if player.pending_dir is not None:
                if not (_room.num_players == 1 and OPPOSITE_DIR[snake.heading] == player.pending_dir):
                    snake.heading = player.pending_dir
                player.pending_dir = None

            hx, hy = snake.cells[0]
            dx, dy = DIR_VECTORS[snake.heading]
            proposed_heads[player.player_id] = (hx + dx, hy + dy)

        # Step 2: Check wall collisions and food consumption.
        food_set = set(_room.food)
        eaten_food: set[tuple[int, int]] = set()
        wall_dead: set[str] = set()

        for player in alive_players:
            pid = player.player_id
            nx, ny = proposed_heads[pid]
            if nx < 0 or nx >= config.GRID_WIDTH or ny < 0 or ny >= config.GRID_HEIGHT:
                wall_dead.add(pid)
            elif (nx, ny) in food_set:
                eaten_food.add((nx, ny))

        # Step 3: Build occupancy map after tail movement (unless growing).
        next_bodies: dict[str, list[tuple[int, int]]] = {}
        for player in alive_players:
            pid = player.player_id
            snake = _room.snakes_by_id[pid]
            if pid in wall_dead:
                continue
            nx, ny = proposed_heads[pid]
            will_eat = (nx, ny) in eaten_food
            if will_eat:
                body = [proposed_heads[pid]] + list(snake.cells)
            elif snake.grow_pending > 0:
                body = [proposed_heads[pid]] + list(snake.cells)
                snake.grow_pending -= 1
            else:
                body = [proposed_heads[pid]] + list(snake.cells[:-1])
            next_bodies[pid] = body

        # Step 4: Detect head-to-head and body collisions.
        body_dead: set[str] = set()
        alive_ids = [p.player_id for p in alive_players if p.player_id not in wall_dead]

        for pid in alive_ids:
            head = next_bodies[pid][0]
            # Self collision (head matches any later segment of own next body).
            if head in next_bodies[pid][1:]:
                body_dead.add(pid)
                continue
            # Collision with another alive snake (head-to-head or head-to-body).
            for other_id in alive_ids:
                if other_id == pid:
                    continue
                if head in next_bodies[other_id]:
                    body_dead.add(pid)
                    break

        newly_dead = wall_dead | body_dead

        # Step 5: Apply deaths, remove dead snakes from board, and award food points.
        for player in alive_players:
            pid = player.player_id
            snake = _room.snakes_by_id[pid]
            if pid in newly_dead:
                player.alive = False
                snake.alive = False
                snake.cells = []
            else:
                snake.cells = next_bodies[pid]
                head = snake.cells[0]
                if head in eaten_food:
                    player.score += 1
                    snake.grow_pending += 1

        if eaten_food:
            _room.food = [pt for pt in _room.food if pt not in eaten_food]
            _spawn_food_locked(max(1, _room.num_players))

        # Step 6: Evaluate end-of-game condition.
        remaining = [p for p in _room.players_by_token.values() if p.alive]
        if _room.num_players == 1:
            if len(remaining) == 0:
                _room.phase = "finished"
                _room.winner = None
        else:
            if len(remaining) <= 1:
                _room.phase = "finished"
                _room.winner = remaining[0].player_id if len(remaining) == 1 else None


def reset_room(token: str | None) -> dict[str, Any]:
    """Reset the room back to lobby after a finished game."""
    with _room_lock:
        _require_player_locked(token)
        if _room.phase != "finished":
            raise GameError(409, "Room can only be reset when the game is finished.")

        _room.phase = "lobby"
        _room.num_players = None
        _room.tick = 0
        _room.players_by_token.clear()
        _room.snakes_by_id.clear()
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

        snakes_payload = [
            {
                "player_id": s.player_id,
                "cells": [[x, y] for x, y in s.cells],
                "alive": s.alive,
            }
            for s in _room.snakes_by_id.values()
            if s.alive
        ]

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