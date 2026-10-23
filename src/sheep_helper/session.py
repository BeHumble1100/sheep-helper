"""Local, manual-play session state built on the exact solver rules.

This module deliberately has no mouse or window automation.  It is the bridge
between a verified offline plan and a player who performs each game action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .solver import (
    Action,
    Board,
    DFSConfig,
    SearchState,
    SolveResult,
    _strip_moves,
    solve_dfs,
    transition,
)


def fresh_state(board: Board) -> SearchState:
    return SearchState(
        remaining=(1 << board.size) - 1,
        available=board.initial_available,
        tray=(),
        parked=((), (), ()),
        move_out_used=False,
        revive_used=False,
        moves=(),
    )


def _action_record(board: Board, action: Action) -> dict[str, object]:
    kind, value = action
    record: dict[str, object] = {"action": kind}
    if kind == "board":
        record.update({"tile_id": board.ids[value], "type": board.types[value]})
    elif kind == "park":
        record["temporary_slot"] = value + 1
    return record


@dataclass
class ManualPlaySession:
    """Track a player-operated board and discard a stale plan safely."""

    board: Board
    state: SearchState = field(init=False)
    plan: tuple[Action, ...] = ()
    last_result: SolveResult | None = None

    def __post_init__(self) -> None:
        self.state = fresh_state(self.board)

    def solve(self, config: DFSConfig = DFSConfig()) -> SolveResult:
        self.last_result = solve_dfs(self.board, config, initial_state=self.state)
        self.plan = self.last_result.moves if self.last_result.solved else ()
        return self.last_result

    def next_action(self) -> Action | None:
        for action in self.plan:
            # The client emits this immediately after the overflowing pick;
            # it is a status marker rather than a separate player click.
            if action[0] != "revive_move_out":
                return action
        return None

    def apply(self, action: Action) -> SearchState:
        """Record one player action and invalidate plans that diverge."""
        if action[0] == "revive_move_out":
            raise ValueError("复活移出由触发第七格的那次点击自动完成")
        next_state = transition(self.board, self.state, action)
        if next_state is None:
            raise ValueError("该操作会在没有可用复活时填满第七格")
        expected = self.next_action()
        self.state = next_state
        if action == expected:
            consumed = 0
            while consumed < len(self.plan):
                current = self.plan[consumed]
                consumed += 1
                if current == action:
                    break
            while consumed < len(self.plan) and self.plan[consumed][0] == "revive_move_out":
                consumed += 1
            self.plan = self.plan[consumed:]
        else:
            self.plan = ()
        return self.state

    def public_state(self) -> dict[str, object]:
        visible = [
            {"tile_id": self.board.ids[index], "type": self.board.types[index]}
            for index in range(self.board.size)
            if self.state.available & (1 << index)
        ]
        return {
            "remaining_tiles": self.state.remaining.bit_count(),
            "visible": visible,
            "tray": [self.board.types[index] for index in self.state.tray],
            "temporary_slots": [
                [self.board.types[index] for index in slot] for slot in self.state.parked
            ],
            "move_out_used": self.state.move_out_used,
            "revive_used": self.state.revive_used,
            "next": None if self.next_action() is None else _action_record(self.board, self.next_action()),
            "planned_actions": len(self.plan),
            "search": None if self.last_result is None else {
                "solved": self.last_result.solved,
                "explored_states": self.last_result.explored_states,
                "reason": self.last_result.reason,
            },
        }


def replay_player_actions(session: ManualPlaySession, actions: Iterable[Action]) -> SearchState:
    for action in actions:
        session.apply(action)
    return session.state
