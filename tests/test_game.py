"""Unit tests for server.game rules, arrow ownership, and collisions."""

import unittest

from server import config, game


class GameEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset room to a clean lobby before every test.
        with game._room_lock:
            game._room.phase = "lobby"
            game._room.num_players = None
            game._room.tick = 0
            game._room.players_by_token.clear()
            game._room.snakes_by_id.clear()
            game._room.food.clear()
            game._room.winner = None

    def _force_finished_and_reset(self, token: str) -> None:
        with game._room_lock:
            game._room.phase = "finished"
        game.reset_room(token)

    def test_single_player_starts_immediately_and_ignores_180_reversal(self) -> None:
        p1 = game.join_player("Solo", 1)
        self.assertEqual(p1["slot"], 1)
        self.assertEqual(
            p1["owned_keys"],
            sorted(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]),
        )

        st = game.get_state(p1["token"])
        self.assertEqual(st["phase"], "running")
        self.assertEqual(len(st["food"]), 1)

        # Snake starts facing RIGHT; ArrowLeft is a 180-degree reversal and must be ignored.
        res = game.submit_input(p1["token"], "ArrowLeft")
        self.assertEqual(res, {"ok": True})
        game.step_tick()

        st_after = game.get_state(p1["token"])
        head = st_after["snakes"][0]["cells"][0]
        # Started at x=15, y=10 facing RIGHT -> after 1 tick head should be at x=16, y=10.
        self.assertEqual(head, [16, 10])

    def test_arrow_ownership_403_and_invalid_token_401(self) -> None:
        p1 = game.join_player("Alice", 2)
        p2 = game.join_player("Bob", 2)

        # P1 owns ArrowUp and ArrowDown only; ArrowRight belongs to P2.
        with self.assertRaises(game.GameError) as ctx403:
            game.submit_input(p1["token"], "ArrowRight")
        self.assertEqual(ctx403.exception.status_code, 403)

        # P2 owns ArrowLeft and ArrowRight; ArrowUp belongs to P1.
        with self.assertRaises(game.GameError) as ctx403_p2:
            game.submit_input(p2["token"], "ArrowUp")
        self.assertEqual(ctx403_p2.exception.status_code, 403)

        # Missing or wrong token returns 401.
        with self.assertRaises(game.GameError) as ctx401:
            game.submit_input("bad-token", "ArrowUp")
        self.assertEqual(ctx401.exception.status_code, 401)

    def test_room_capacity_409_and_last_input_in_tick_wins(self) -> None:
        p1 = game.join_player("One", 2)
        p2 = game.join_player("Two", 2)

        with self.assertRaises(game.GameError) as ctx409:
            game.join_player("Third", 2)
        self.assertEqual(ctx409.exception.status_code, 409)

        # P1 starts at (6, 10) facing RIGHT.
        # ArrowUp -> CCW (UP), ArrowDown -> CW (DOWN).
        # Queue ArrowUp then ArrowDown in the same tick; ArrowDown (last valid) must win.
        game.submit_input(p1["token"], "ArrowUp")
        game.submit_input(p1["token"], "ArrowDown")
        game.step_tick()

        st = game.get_state(p1["token"])
        p1_snake = next(s for s in st["snakes"] if s["player_id"] == p1["player_id"])
        self.assertEqual(p1_snake["cells"][0], [6, 11])

        self._force_finished_and_reset(p2["token"])
        self.assertEqual(game.get_state(None)["phase"], "lobby")

    def test_wall_collision_and_food_scoring(self) -> None:
        p1 = game.join_player("Runner", 1)
        with game._room_lock:
            snake = game._room.snakes_by_id[p1["player_id"]]
            snake.cells = [(config.GRID_WIDTH - 1, 5), (config.GRID_WIDTH - 2, 5), (config.GRID_WIDTH - 3, 5)]
            snake.heading = "RIGHT"
            _room_food = [(config.GRID_WIDTH - 1, 5)]
            game._room.food = []

        # Place food directly ahead at (GRID_WIDTH - 1, 5) to verify +1 score & growth.
        with game._room_lock:
            snake2 = game._room.snakes_by_id[p1["player_id"]]
            snake2.cells = [(10, 10), (9, 10), (8, 10)]
            snake2.heading = "RIGHT"
            game._room.food = [(11, 10)]

        game.step_tick()
        st_eat = game.get_state(p1["token"])
        self.assertEqual(st_eat["players"][0]["score"], 1)
        self.assertEqual(len(st_eat["snakes"][0]["cells"]), 4)

        # Move snake into right wall -> dies and single-player game finishes.
        with game._room_lock:
            snake3 = game._room.snakes_by_id[p1["player_id"]]
            snake3.cells = [(config.GRID_WIDTH - 1, 4), (config.GRID_WIDTH - 2, 4), (config.GRID_WIDTH - 3, 4)]
            snake3.heading = "RIGHT"

        game.step_tick()
        st_end = game.get_state(p1["token"])
        self.assertEqual(st_end["phase"], "finished")
        self.assertFalse(st_end["players"][0]["alive"])
        self.assertEqual(st_end["snakes"], [])

    def test_head_to_head_collision_in_multiplayer(self) -> None:
        p1 = game.join_player("Alpha", 2)
        p2 = game.join_player("Beta", 2)

        with game._room_lock:
            s1 = game._room.snakes_by_id[p1["player_id"]]
            s2 = game._room.snakes_by_id[p2["player_id"]]
            s1.cells = [(14, 10), (13, 10), (12, 10)]
            s1.heading = "RIGHT"
            s2.cells = [(16, 10), (17, 10), (18, 10)]
            s2.heading = "LEFT"

        game.step_tick()
        st = game.get_state(p1["token"])
        self.assertEqual(st["phase"], "finished")
        self.assertIsNone(st["winner"])
        self.assertEqual(st["snakes"], [])


if __name__ == "__main__":
    unittest.main()