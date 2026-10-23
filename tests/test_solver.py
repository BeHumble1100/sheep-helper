from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from sheep_helper.solver import (
    _state_key,
    Board,
    DFSConfig,
    MCTSConfig,
    SearchConfig,
    SearchState,
    SolveResult,
    replay,
    solve,
    solve_batches,
    solve_counts,
    solve_dfs,
    solve_hybrid,
    solve_mcts,
    transition,
)
from sheep_helper.session import ManualPlaySession


ROOT = Path(__file__).resolve().parents[1]


def tiny_board() -> Board:
    return Board(
        ids=("a", "b", "c", "d", "e", "f"),
        types=(1, 2, 3, 1, 2, 3),
        blocker_masks=(0, 0, 0, 0, 0, 0),
        dependents=((), (), (), (), (), ()),
        initial_available=(1 << 6) - 1,
        type_values=(1, 2, 3),
        type_indexes={1: 0, 2: 1, 3: 2},
    )


def initial_state(board: Board) -> SearchState:
    return SearchState(
        remaining=(1 << board.size) - 1,
        available=board.initial_available,
        tray=(),
        parked=((), (), ()),
        move_out_used=False,
        revive_used=False,
        moves=(),
    )


def check_move_out_preserves_order_and_parked_cards_can_return() -> None:
    board = tiny_board()
    state = initial_state(board)
    for card in (0, 1, 2):
        state = transition(board, state, ("board", card))
        assert state is not None
    state = transition(board, state, ("move_out", -1))
    assert state is not None
    assert state.tray == ()
    assert state.parked == ((0,), (1,), (2,))
    state = transition(board, state, ("park", 1))
    assert state is not None
    assert state.tray == (1,)
    assert state.parked == ((0,), (), (2,))


def check_decoded_first_map_has_a_replayable_solution() -> None:
    board = Board.from_files(
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
    )
    result = solve(board, SearchConfig(
        beam_width=300,
        restarts=2,
        rollout_attempts=50,
        time_limit_seconds=5,
    ))
    assert result.solved
    final_state = replay(board, result.moves)
    assert final_state.remaining == 0
    assert not final_state.tray
    assert not any(final_state.parked)


def check_sp_mcts_returns_a_replayable_plan_for_decoded_sample() -> None:
    board = Board.from_files(
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
    )
    result = solve_mcts(board, MCTSConfig(iterations=20_000, time_limit_seconds=5))
    assert result.solved
    final_state = replay(board, result.moves)
    assert final_state.remaining == 0
    assert not final_state.tray and not any(final_state.parked)


def check_heuristic_dfs_returns_a_replayable_plan_for_decoded_sample() -> None:
    board = Board.from_files(
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
    )
    result = solve_dfs(board, DFSConfig(time_limit_seconds=5, max_states=50_000))
    assert result.solved
    final_state = replay(board, result.moves)
    assert final_state.remaining == 0
    assert not final_state.tray and not any(final_state.parked)


def check_manual_session_advances_a_verified_plan_and_reports_next_step() -> None:
    board = Board.from_files(
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
        ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
    )
    session = ManualPlaySession(board)
    result = session.solve(DFSConfig(time_limit_seconds=5, max_states=50_000))
    assert result.solved
    first = session.next_action()
    assert first is not None and first[0] == "board"
    session.apply(first)
    assert session.state.remaining.bit_count() == board.size - 1
    assert session.public_state()["next"] is not None


class SolverTests(unittest.TestCase):
    def test_tray_order_merges_only_after_both_tools_are_used(self) -> None:
        board = tiny_board()
        base = initial_state(board)
        first = SearchState(base.remaining, base.available, (0, 1), base.parked, True, True, ())
        second = SearchState(base.remaining, base.available, (1, 0), base.parked, True, True, ())
        self.assertEqual(_state_key(board, first), _state_key(board, second))
        before = SearchState(base.remaining, base.available, (0, 1), base.parked, True, False, ())
        reordered = SearchState(base.remaining, base.available, (1, 0), base.parked, True, False, ())
        self.assertNotEqual(_state_key(board, before), _state_key(board, reordered))

    def test_parked_slots_merge_only_after_both_tools_are_used(self) -> None:
        board = tiny_board()
        base = initial_state(board)
        slots = ((0, 1), (2,), ())
        swapped = ((), (0, 1), (2,))
        exhausted = SearchState(base.remaining, base.available, (), slots, True, True, ())
        reordered = SearchState(base.remaining, base.available, (), swapped, True, True, ())
        self.assertEqual(_state_key(board, exhausted), _state_key(board, reordered))
        pending = SearchState(base.remaining, base.available, (), slots, True, False, ())
        pending_reordered = SearchState(base.remaining, base.available, (), swapped, True, False, ())
        self.assertNotEqual(_state_key(board, pending), _state_key(board, pending_reordered))

    def test_move_out_can_park_one_or_two_cards(self) -> None:
        board = tiny_board()
        state = initial_state(board)
        for count, card in ((1, 0), (2, 1)):
            state = transition(board, state, ("board", card))
            self.assertIsNotNone(state)
            moved = transition(board, state, ("move_out", -1))
            self.assertIsNotNone(moved)
            self.assertEqual(moved.tray, ())
            self.assertEqual(moved.parked, tuple(
                (state.tray[slot],) if slot < count else () for slot in range(3)
            ))

    def test_hybrid_stitches_and_replays_prefix_and_tool_suffix(self) -> None:
        board = Board.from_files(
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
        )
        full = solve_batches(board, DFSConfig(time_limit_seconds=5, max_branches=40)).moves
        prefix, suffix = full[:3], full[3:]
        with patch("sheep_helper.solver.solve_counts", return_value=SolveResult(False, prefix, 1, None, 0.01, "partial")), \
             patch("sheep_helper.solver.solve_dfs", return_value=SolveResult(True, suffix, 1, 0, 0.01, "solved")):
            result = solve_hybrid(board, DFSConfig(time_limit_seconds=5, restarts=1))
        self.assertTrue(result.solved)
        self.assertEqual(result.moves, full)
        self.assertEqual(replay(board, result.moves).remaining, 0)

    def test_count_state_solver_replays_tutorial_board_without_tools(self) -> None:
        board = Board.from_files(
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
        )
        result = solve_counts(board, DFSConfig(time_limit_seconds=5, max_branches=15))
        self.assertTrue(result.solved)
        self.assertEqual(replay(board, result.moves).remaining, 0)
        self.assertTrue(all(action == "board" for action, _card in result.moves))

    def test_count_state_observer_only_receives_replayable_prefixes(self) -> None:
        board = Board.from_files(
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
        )
        samples = []
        result = solve_counts(
            board,
            DFSConfig(time_limit_seconds=5, max_branches=15),
            prefix_observer=lambda path, remaining, occupied: samples.append((path, remaining, occupied)),
        )
        self.assertTrue(result.solved)
        self.assertTrue(samples)
        for path, remaining, occupied in samples:
            state = replay(board, path)
            self.assertEqual(state.remaining, remaining)
            self.assertEqual(len(state.tray), occupied)

    def test_triple_batch_solver_replays_tutorial_board(self) -> None:
        board = Board.from_files(
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
            ROOT / "captured-layouts" / "f35bb0aff5ff7fef951795a377cf0749.coverage.json",
        )
        result = solve_batches(board, DFSConfig(time_limit_seconds=5, max_branches=40))
        self.assertTrue(result.solved)
        self.assertEqual(replay(board, result.moves).remaining, 0)
        self.assertEqual(len(result.moves), board.size)

    def test_move_out_preserves_order_and_parked_cards_can_return(self) -> None:
        check_move_out_preserves_order_and_parked_cards_can_return()

    def test_decoded_first_map_has_a_replayable_solution(self) -> None:
        check_decoded_first_map_has_a_replayable_solution()

    def test_sp_mcts_returns_a_replayable_plan_for_decoded_sample(self) -> None:
        check_sp_mcts_returns_a_replayable_plan_for_decoded_sample()

    def test_heuristic_dfs_returns_a_replayable_plan_for_decoded_sample(self) -> None:
        check_heuristic_dfs_returns_a_replayable_plan_for_decoded_sample()

    def test_manual_session_advances_a_verified_plan_and_reports_next_step(self) -> None:
        check_manual_session_advances_a_verified_plan_and_reports_next_step()
