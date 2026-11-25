"""Unit tests for cooperative server.game rules, arrow ownership, and collisions."""

import unittest

from server import config, game


class CooperativeGameEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        with game._room_lock:
            game._room.phase = "lobby"
            game._room.num_players = None
            game._room.tick = 0
            game._room.players_by_token.clear()
            game._room.shared_snake = None
            game._room.pending_dir = None
            game._room.team_score = 0
            game._room.food.clear()
            game._room.winner = None

    def _force_finished_and_reset(self, token: str) -> None:
        with game._room_lock:
            game._room.phase = "finished"
        game.reset_room(token)

    def test_pregame_lobby_exposes_owned_keys_before_start(self) -> None:
        p1 = game.join_player("Coop1", 2)
        self.assertEqual(p1["owned_keys"], ["ArrowDown", "ArrowUp"])

        st = game.get_state(p1["token"])
        self.assertEqual(st["phase"], "lobby")
        self.assertEqual(st["joined"], 1)
        self.assertEqual(st["needed"], 2)
        self.assertEqual(st["you"]["owned_keys"], ["ArrowDown", "ArrowUp"])

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

        start_x, start_y = st["snakes"][0]["cells"][0]
        game.submit_input(p1["token"], "ArrowLeft")
        game.step_tick()

        st_after = game.get_state(p1["token"])
        self.assertEqual(st_after["snakes"][0]["cells"][0], [start_x + 1, start_y])

    def test_coop_shared_snake_arrow_ownership_403_and_last_input_wins(self) -> None:
        p1 = game.join_player("Alice", 2)
        p2 = game.join_player("Bob", 2)

        with self.assertRaises(game.GameError) as ctx403:
            game.submit_input(p1["token"], "ArrowRight")
        self.assertEqual(ctx403.exception.status_code, 403)

        with self.assertRaises(game.GameError) as ctx401:
            game.submit_input("bad-token", "ArrowUp")
        self.assertEqual(ctx401.exception.status_code, 401)

        # Shared snake starts at (15, 10) facing RIGHT.
        # P1 ArrowUp -> CCW (UP), P2 ArrowRight -> CW (DOWN). Last valid input in tick wins.
        game.submit_input(p1["token"], "ArrowUp")
        game.submit_input(p2["token"], "ArrowRight")
        game.step_tick()

        st = game.get_state(p1["token"])
        self.assertEqual(len(st["snakes"]), 1)
        self.assertEqual(st["snakes"][0]["cells"][0], [15, 11])

        self._force_finished_and_reset(p1["token"])
        self.assertEqual(game.get_state(None)["phase"], "lobby")


def test_coop_food_growth_and_wall_collision_finishes_game(self) -> None:
        p1 = game.join_player("CoopA", 2)
        p2 = game.join_player("CoopB", 2)

        with game._room_lock:
            assert game._room.shared_snake is not None
            game._room.shared_snake.cells = [(10, 10), (9, 10), (8, 10)]
            game._room.shared_snake.heading = "RIGHT"
            game._room.food = [(11, 10)]
            game._room.last_steer_player_id = p2["player_id"]

        game.step_tick()
        st_eat = game.get_state(p1["token"])
        self.assertEqual(st_eat["players"][0]["score"], 0)
        self.assertEqual(st_eat["players"][1]["score"], 1)
        self.assertEqual(
            sum(p["score"] for p in st_eat["players"]),
            1,
        )
        self.assertEqual(len(st_eat["snakes"][0]["cells"]), 4)

        with game._room_lock:
            assert game._room.shared_snake is not None
            game._room.shared_snake.cells = [
                (config.GRID_WIDTH - 1, 5),
                (config.GRID_WIDTH - 2, 5),
                (config.GRID_WIDTH - 3, 5),
            ]
            game._room.shared_snake.heading = "RIGHT"

        game.step_tick()
        st_end = game.get_state(p2["token"])
        self.assertEqual(st_end["phase"], "finished")
        self.assertEqual(st_end["snakes"], [])

if __name__ == "__main__":
    unittest.main()
