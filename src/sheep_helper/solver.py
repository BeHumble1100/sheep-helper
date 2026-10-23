"""Offline solver for fully decoded three-match tile layouts.

The client inserts each picked card immediately after the last card of the
same type, then removes any adjacent triple. Unlike a count-only model, this
keeps the ordered tray because move-out and revive park its first three cards
in three individually recoverable slots.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
import math
from pathlib import Path
import random
import time
from typing import Callable, Iterable, Literal


TRAY_LIMIT = 6
ActionKind = Literal["board", "park", "move_out", "revive_move_out"]
Action = tuple[ActionKind, int]


@dataclass(frozen=True)
class Board:
    ids: tuple[str, ...]
    types: tuple[int, ...]
    blocker_masks: tuple[int, ...]
    dependents: tuple[tuple[int, ...], ...]
    initial_available: int
    type_values: tuple[int, ...]
    type_indexes: dict[int, int]
    layers: tuple[int, ...] = ()

    @property
    def size(self) -> int:
        return len(self.ids)

    @classmethod
    def from_files(cls, cards_path: Path, coverage_path: Path) -> "Board":
        cards = json.loads(cards_path.read_text(encoding="utf-8"))
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        card_map = cards.get("source_map_md5")
        cover_map = coverage.get("source_map_md5")
        if card_map is not None or cover_map is not None:
            if not card_map or card_map != cover_map:
                raise ValueError("cards 与 coverage 的 map_md5 不一致")
        seed = cards.get("map_seed")
        if seed is not None and (
            not isinstance(seed, list)
            or len(seed) != 4
            or any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in seed)
            or not any(seed)
        ):
            raise ValueError("cards 中的 map_seed 无效")
        tiles = cards["tiles"]
        coverage_tiles = coverage["tiles"]
        if len(tiles) != len(coverage_tiles):
            raise ValueError("cards 与 coverage 的牌数不一致")
        ids = tuple(str(tile["id"]) for tile in tiles)
        index = {tile_id: position for position, tile_id in enumerate(ids)}
        if len(index) != len(ids):
            raise ValueError("地图中存在重复 tile id")
        coverage_by_id = {str(tile["id"]): tile for tile in coverage_tiles}
        if set(index) != set(coverage_by_id):
            raise ValueError("cards 与 coverage 的 tile id 集合不一致")

        blocker_masks: list[int] = []
        dependents: list[list[int]] = [[] for _ in ids]
        for tile_id in ids:
            mask = 0
            for blocker_id in coverage_by_id[tile_id]["blockers"]:
                blocker_index = index[str(blocker_id)]
                mask |= 1 << blocker_index
                dependents[blocker_index].append(index[tile_id])
            blocker_masks.append(mask)
        initial_available = sum(
            1 << position
            for position, mask in enumerate(blocker_masks)
            if mask == 0
        )
        types = tuple(int(tile["type"]) for tile in tiles)
        if any(card_type <= 0 for card_type in types):
            raise ValueError("cards 中存在未识别牌面")
        type_values = tuple(sorted(set(types)))
        if any(types.count(card_type) % 3 for card_type in type_values):
            raise ValueError("每种牌的总数必须是 3 的倍数")
        return cls(
            ids=ids,
            types=types,
            blocker_masks=tuple(blocker_masks),
            dependents=tuple(tuple(values) for values in dependents),
            initial_available=initial_available,
            type_values=type_values,
            type_indexes={value: position for position, value in enumerate(type_values)},
            layers=tuple(int(tile["layer"]) for tile in tiles),
        )


@dataclass(frozen=True)
class SearchConfig:
    beam_width: int = 5_000
    restarts: int = 12
    time_limit_seconds: float = 75.0
    seed: int = 20261021
    allow_move_out: bool = True
    allow_revive: bool = True
    rollout_attempts: int = 1_500


@dataclass(frozen=True)
class SearchState:
    remaining: int
    available: int
    tray: tuple[int, ...]
    parked: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]
    move_out_used: bool
    revive_used: bool
    moves: tuple[Action, ...]


@dataclass(frozen=True)
class SolveResult:
    solved: bool
    moves: tuple[Action, ...]
    explored_states: int
    restart: int | None
    elapsed_seconds: float
    reason: str


@dataclass(frozen=True)
class MCTSConfig:
    iterations: int = 250_000
    time_limit_seconds: float = 180.0
    exploration: float = 1.25
    seed: int = 20261021
    allow_move_out: bool = True
    allow_revive: bool = True


@dataclass(frozen=True)
class DFSConfig:
    """Limits for a deterministic, heuristic-first depth-first search.

    Unlike the beam search, DFS commits to the most promising chain before
    backtracking.  That is useful here because a board is fully observable and
    a move can immediately expose a long, matching sequence.
    """

    time_limit_seconds: float = 180.0
    max_states: int = 10_000_000
    max_branches: int = 7
    restarts: int = 8
    seed: int = 20261021
    allow_move_out: bool = True
    allow_revive: bool = True
    move_out_min_tray: int = 5
    order_jitter: float = 12.0
    layer_weight: float = 2.0


def _iter_bits(mask: int) -> Iterable[int]:
    while mask:
        least = mask & -mask
        yield least.bit_length() - 1
        mask ^= least


def _next_available(board: Board, state: SearchState, chosen: int) -> int:
    available = state.available & ~(1 << chosen)
    remaining = state.remaining & ~(1 << chosen)
    for dependent in board.dependents[chosen]:
        bit = 1 << dependent
        if remaining & bit and board.blocker_masks[dependent] & remaining == 0:
            available |= bit
    return available


def _insert_and_crush(board: Board, tray: tuple[int, ...], card: int) -> tuple[int, ...]:
    """Mirror getNewPosit followed by isCrushBlock in the game client."""
    card_type = board.types[card]
    insert_at = len(tray)
    for position in range(len(tray) - 1, -1, -1):
        if board.types[tray[position]] == card_type:
            insert_at = position + 1
            break
    updated = tray[:insert_at] + (card,) + tray[insert_at:]
    for position in range(len(updated) - 2):
        card_type = board.types[updated[position]]
        if (
            board.types[updated[position + 1]] == card_type
            and board.types[updated[position + 2]] == card_type
        ):
            return updated[:position] + updated[position + 3:]
    return updated


def _park_first_three(state: SearchState) -> tuple[
    tuple[int, ...], tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]
]:
    if not 1 <= len(state.tray) <= TRAY_LIMIT:
        raise ValueError("移出道具要求消除栏有 1 到 6 张牌")
    cards = state.tray[:3]
    parked = tuple(
        state.parked[slot] + ((cards[slot],) if slot < len(cards) else ())
        for slot in range(3)
    )
    return state.tray[3:], parked  # type: ignore[return-value]


def _apply_card(
    board: Board,
    state: SearchState,
    card: int,
    action: Action,
    remaining: int,
    available: int,
    parked: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]],
    allow_revive: bool,
) -> SearchState | None:
    tray = _insert_and_crush(board, state.tray, card)
    revive_used = state.revive_used
    moves = state.moves + (action,)
    if len(tray) > TRAY_LIMIT:
        if not allow_revive or revive_used or len(tray) != 7:
            return None
        first_three = tray[:3]
        parked = tuple(parked[slot] + (first_three[slot],) for slot in range(3))
        tray = tray[3:]
        revive_used = True
        moves += (("revive_move_out", -1),)
    return SearchState(
        remaining=remaining,
        available=available,
        tray=tray,
        parked=parked,
        move_out_used=state.move_out_used,
        revive_used=revive_used,
        moves=moves,
    )


def transition(
    board: Board, state: SearchState, action: Action, *, allow_revive: bool = True
) -> SearchState | None:
    """Apply a legal board pick, temporary-slot return, or move-out prop."""
    kind, value = action
    if kind == "board":
        if value < 0 or value >= board.size or not state.available & (1 << value):
            raise ValueError(f"tile {value} is not currently visible")
        return _apply_card(
            board, state, value, action,
            state.remaining & ~(1 << value),
            _next_available(board, state, value),
            state.parked, allow_revive,
        )
    if kind == "park":
        if value not in (0, 1, 2) or not state.parked[value]:
            raise ValueError(f"temporary slot {value + 1} is empty")
        card = state.parked[value][-1]
        parked = list(state.parked)
        parked[value] = parked[value][:-1]
        return _apply_card(
            board, state, card, action, state.remaining, state.available,
            tuple(parked), allow_revive,  # type: ignore[arg-type]
        )
    if kind == "move_out":
        if state.move_out_used:
            return None
        tray, parked = _park_first_three(state)
        return SearchState(
            state.remaining, state.available, tray, parked, True,
            state.revive_used, state.moves + (action,),
        )
    raise ValueError(f"unsupported action {kind}")


def _visible_type_counts(board: Board, available: int) -> tuple[int, ...]:
    counts = [0] * len(board.type_values)
    for position in _iter_bits(available):
        counts[board.type_indexes[board.types[position]]] += 1
    return tuple(counts)


def _state_score(board: Board, state: SearchState, rng: random.Random) -> float:
    visible = _visible_type_counts(board, state.available)
    tray_counts = [0] * len(board.type_values)
    for card in state.tray:
        tray_counts[board.type_indexes[board.types[card]]] += 1
    pair_matches = sum(visible[index] for index, count in enumerate(tray_counts) if count == 2)
    single_matches = sum(visible[index] for index, count in enumerate(tray_counts) if count == 1)
    parked_count = sum(len(slot) for slot in state.parked)
    no_match_risk = len(state.tray) >= 4 and pair_matches + single_matches == 0
    return (
        24.0 * pair_matches + 5.0 * single_matches
        + 1.4 * state.available.bit_count() - 16.0 * len(state.tray)
        - 1.5 * parked_count - (32.0 if no_match_risk else 0.0)
        + rng.random() * 0.01
    )


def _card_action_score(board: Board, state: SearchState, card: int, rng: random.Random) -> float:
    matching = sum(board.types[existing] == board.types[card] for existing in state.tray)
    completion = 100.0 if matching == 2 else 18.0 if matching == 1 else 0.0
    penalty = 12.0 if matching == 0 and len(state.tray) >= 4 else 0.0
    reveal_bonus = 0.0
    next_remaining = state.remaining & ~(1 << card)
    for dependent in board.dependents[card]:
        if board.blocker_masks[dependent] & next_remaining:
            continue
        revealed_matches = sum(
            board.types[existing] == board.types[dependent] for existing in state.tray
        )
        reveal_bonus += 18.0 if revealed_matches == 2 else 4.0 if revealed_matches == 1 else 0.8
    return completion + reveal_bonus + 1.2 * len(board.dependents[card]) - penalty + rng.random() * 0.01


def _state_key(board: Board, state: SearchState) -> tuple[object, ...]:
    """Collapse histories that differ only in identities of equal-type cards."""
    tray_types = tuple(board.types[card] for card in state.tray)
    parked_types = tuple(tuple(board.types[card] for card in slot) for slot in state.parked)
    if state.move_out_used and state.revive_used:
        # Once neither tool can move the first three cards, only each type's
        # count matters. The three parked stacks are then interchangeable too:
        # no future action can add to a particular numbered slot.
        tray_types = tuple(sorted(tray_types))
        parked_types = tuple(sorted(parked_types))
    return (
        state.remaining,
        tray_types,
        parked_types,
        state.move_out_used,
        state.revive_used,
    )


def _rollout_actions(board: Board, state: SearchState, config: SearchConfig, rng: random.Random) -> list[tuple[float, Action]]:
    """A stochastic, domain-guided playout policy used before beam search."""
    actions: list[tuple[float, Action]] = []
    for card in _iter_bits(state.available):
        score = _card_action_score(board, state, card, rng)
        if len(state.tray) >= 5 and all(board.types[item] != board.types[card] for item in state.tray):
            score -= 80.0
        actions.append((score, ("board", card)))
    for slot, cards in enumerate(state.parked):
        if cards:
            actions.append((_card_action_score(board, state, cards[-1], rng) + 4.0, ("park", slot)))
    if config.allow_move_out and not state.move_out_used and len(state.tray) >= 3:
        actions.append(((65.0 if len(state.tray) >= 5 else -25.0), ("move_out", -1)))
    return actions


def _randomized_rollout(board: Board, config: SearchConfig, rng: random.Random) -> tuple[Action, ...] | None:
    state = SearchState((1 << board.size) - 1, board.initial_available, (), ((), (), ()), False, False, ())
    for _ in range(board.size + 10):
        if state.remaining == 0 and not state.tray and not any(state.parked):
            return state.moves
        scored = _rollout_actions(board, state, config, rng)
        if not scored:
            return None
        floor = max(score for score, _action in scored)
        # Softmax is deliberately mild: matching a pair is favored, while the
        # random tail still lets restarts change which layers are uncovered.
        weights = [max(0.01, 1.0 + (score - floor + 50.0) / 25.0) for score, _action in scored]
        action = rng.choices([action for _score, action in scored], weights=weights, k=1)[0]
        next_state = transition(board, state, action, allow_revive=config.allow_revive)
        if next_state is None:
            return None
        state = next_state
    return None


def solve(board: Board, config: SearchConfig = SearchConfig()) -> SolveResult:
    """Find a complete plan, including optional move-out and revive actions."""
    if board.size == 0:
        return SolveResult(True, (), 0, 0, 0.0, "empty board")
    started = time.monotonic()
    all_remaining = (1 << board.size) - 1
    explored = 0
    rollout_rng = random.Random(config.seed)
    for attempt in range(config.rollout_attempts):
        if time.monotonic() - started >= config.time_limit_seconds * 0.35:
            break
        moves = _randomized_rollout(board, config, rollout_rng)
        explored += board.size
        if moves is not None:
            return SolveResult(True, moves, explored, -(attempt + 1), time.monotonic() - started, "randomized rollout solved")
    for restart in range(config.restarts):
        if time.monotonic() - started >= config.time_limit_seconds:
            break
        rng = random.Random(config.seed + restart)
        frontier = [SearchState(all_remaining, board.initial_available, (), ((), (), ()), False, False, ())]
        # Board picks strictly reduce remaining; props/returns add at most ten actions.
        for _depth in range(board.size + 10):
            if time.monotonic() - started >= config.time_limit_seconds:
                break
            candidates: dict[tuple[object, ...], tuple[float, SearchState]] = {}
            for state in frontier:
                actions: list[Action] = [("board", card) for card in _iter_bits(state.available)]
                actions.extend(("park", slot) for slot, cards in enumerate(state.parked) if cards)
                if config.allow_move_out and not state.move_out_used and len(state.tray) >= 3:
                    actions.append(("move_out", -1))
                actions.sort(
                    key=lambda action: (
                        (70.0 if len(state.tray) >= 5 else -30.0) if action[0] == "move_out" else
                        _card_action_score(
                            board, state,
                            action[1] if action[0] == "board" else state.parked[action[1]][-1],
                            rng,
                        )
                    ), reverse=True,
                )
                for action in actions:
                    child = transition(board, state, action, allow_revive=config.allow_revive)
                    if child is None:
                        continue
                    explored += 1
                    if child.remaining == 0 and not child.tray and not any(child.parked):
                        return SolveResult(True, child.moves, explored, restart, time.monotonic() - started, "solved")
                    score = _state_score(board, child, rng)
                    key = _state_key(board, child)
                    old = candidates.get(key)
                    if old is None or score > old[0]:
                        candidates[key] = (score, child)
            if not candidates:
                break
            frontier = [
                state for _score, state in heapq.nlargest(
                    config.beam_width, candidates.values(), key=lambda item: item[0]
                )
            ]
    return SolveResult(False, (), explored, None, time.monotonic() - started, "time limit or beam exhausted")


def _dfs_action_score(
    board: Board, state: SearchState, action: Action, rng: random.Random,
    order_jitter: float = 0.0,
    layer_weight: float = 0.0,
) -> float:
    """Order DFS moves by immediate clearance, safe capacity, and unlocking.

    This is deliberately an ordering heuristic rather than an assertion about
    solvability: bounded DFS still backtracks when the attractive route dies.
    """
    if action[0] == "move_out":
        # Keeping the prop until the tray is under pressure preserves it for
        # forced mismatches, while still exploring it before a likely overflow.
        return 110.0 if len(state.tray) >= 6 else 28.0
    card = action[1] if action[0] == "board" else state.parked[action[1]][-1]
    card_type = board.types[card]
    held = sum(board.types[item] == card_type for item in state.tray)
    completes = 180.0 if held == 2 else 72.0 if held == 1 else 0.0
    pressure = 42.0 if len(state.tray) >= 5 and held else 0.0
    risk = 65.0 if len(state.tray) >= 5 and not held else 0.0
    unlocks = 0.0
    if action[0] == "board":
        remaining = state.remaining & ~(1 << card)
        for dependent in board.dependents[card]:
            if board.blocker_masks[dependent] & remaining:
                continue
            revealed_type = board.types[dependent]
            matches = sum(board.types[item] == revealed_type for item in state.tray)
            unlocks += 16.0 if matches == 2 else 5.0 if matches == 1 else 1.0
    parked_bonus = 8.0 if action[0] == "park" and held else 0.0
    layer_bonus = (
        layer_weight * board.layers[card]
        if action[0] == "board" and board.layers else 0.0
    )
    return completes + pressure + unlocks + parked_bonus + layer_bonus - risk + rng.random() * order_jitter


def solve_dfs(
    board: Board,
    config: DFSConfig = DFSConfig(),
    initial_state: SearchState | None = None,
) -> SolveResult:
    """Run bounded duplicate-pruned DFS on the exact tray-and-tools model.

    It is intentionally incomplete when ``max_branches`` is finite.  A miss
    therefore means only that this ordered search budget did not find a plan.
    """
    if board.size == 0:
        return SolveResult(True, (), 0, 0, 0.0, "empty board")
    if config.max_branches < 1 or config.restarts < 1:
        raise ValueError("max_branches and restarts must be positive")
    if not 1 <= config.move_out_min_tray <= TRAY_LIMIT:
        raise ValueError("move_out_min_tray must be between 1 and 6")

    started = time.monotonic()
    explored = 0
    all_remaining = (1 << board.size) - 1
    best_moves: tuple[Action, ...] = ()
    restarts_run = 0

    for restart in range(config.restarts):
        if explored >= config.max_states or time.monotonic() - started >= config.time_limit_seconds:
            break
        restarts_run += 1
        restart_deadline = started + config.time_limit_seconds * (restart + 1) / config.restarts
        restart_state_limit = config.max_states * (restart + 1) // config.restarts
        rng = random.Random(config.seed + restart)
        seen: set[tuple[object, ...]] = set()
        initial = _strip_moves(initial_state) if initial_state is not None else SearchState(
            all_remaining, board.initial_available, (), ((), (), ()), False, False, ()
        )
        path: list[Action] = []

        def visit(state: SearchState) -> tuple[Action, ...] | None:
            nonlocal explored, best_moves
            explored += 1
            if explored % 512 == 0 and (
                explored >= restart_state_limit
                or time.monotonic() >= restart_deadline
            ):
                return None
            if len(path) > len(best_moves):
                best_moves = tuple(path)
            if state.remaining == 0 and not state.tray and not any(state.parked):
                return tuple(path)
            key = _state_key(board, state)
            if key in seen:
                return None
            seen.add(key)

            actions: list[Action] = [("board", card) for card in _iter_bits(state.available)]
            actions.extend(("park", slot) for slot, cards in enumerate(state.parked) if cards)
            if (
                config.allow_move_out
                and not state.move_out_used
                and len(state.tray) >= config.move_out_min_tray
            ):
                actions.append(("move_out", -1))
            actions.sort(
                key=lambda action: _dfs_action_score(
                    board, state, action, rng, config.order_jitter, config.layer_weight
                ),
                reverse=True,
            )

            # All actions that instantly complete a triple are retained even
            # when the ordinary branch cap is small.
            selected = actions[:config.max_branches]
            for action in actions[config.max_branches:]:
                if action[0] == "move_out":
                    continue
                card = action[1] if action[0] == "board" else state.parked[action[1]][-1]
                if sum(board.types[item] == board.types[card] for item in state.tray) == 2:
                    selected.append(action)
            for action in selected:
                child = transition(board, state, action, allow_revive=config.allow_revive)
                if child is None:
                    continue
                # Keep history on the recursion stack instead of copying an
                # ever-growing tuple into every candidate state.  ``transition``
                # emits an additional revive marker only when that pick
                # overflows the seventh slot, so retain both emitted actions.
                emitted = child.moves
                path.extend(emitted)
                result = visit(_strip_moves(child))
                if result is not None:
                    return result
                del path[-len(emitted):]
                if explored >= restart_state_limit or time.monotonic() >= restart_deadline:
                    return None
            return None

        solved = visit(initial)
        if solved is not None:
            return SolveResult(
                True, solved, explored, restart, time.monotonic() - started,
                "heuristic DFS solved",
            )
    return SolveResult(
        False, best_moves, explored, None, time.monotonic() - started,
        f"heuristic DFS budget exhausted after {restarts_run} restarts",
    )


def solve_batches(board: Board, config: DFSConfig = DFSConfig()) -> SolveResult:
    """Search for a zero-tray route made of consecutive same-type triples.

    This deliberately explores a restricted subset of legal play. A miss says
    nothing about routes that carry partial triples or use the two tools.
    """
    started = time.monotonic()
    all_remaining = (1 << board.size) - 1
    explored = 0
    best: tuple[Action, ...] = ()
    seen: set[int] = set()
    path: list[Action] = []
    deadline = started + config.time_limit_seconds

    def remove(remaining: int, available: int, card: int) -> tuple[int, int]:
        remaining &= ~(1 << card)
        available &= ~(1 << card)
        for dependent in board.dependents[card]:
            bit = 1 << dependent
            if remaining & bit and board.blocker_masks[dependent] & remaining == 0:
                available |= bit
        return remaining, available

    def visit(remaining: int, available: int) -> bool:
        nonlocal explored, best
        explored += 1
        if explored % 256 == 0 and (explored >= config.max_states or time.monotonic() >= deadline):
            return False
        if len(path) > len(best):
            best = tuple(path)
        if remaining == 0:
            return True
        if remaining in seen:
            return False
        seen.add(remaining)

        triples: dict[int, tuple[float, tuple[int, int, int], int, int]] = {}
        for first in _iter_bits(available):
            kind = board.types[first]
            after_first, visible_first = remove(remaining, available, first)
            for second in _iter_bits(visible_first):
                if board.types[second] != kind:
                    continue
                after_second, visible_second = remove(after_first, visible_first, second)
                for third in _iter_bits(visible_second):
                    if board.types[third] != kind:
                        continue
                    after_third, visible_third = remove(after_second, visible_second, third)
                    # The same resulting board needs only one legal ordering.
                    if after_third in triples:
                        continue
                    unlocks = visible_third.bit_count() - available.bit_count() + 3
                    pressure = sum(len(board.dependents[card]) for card in (first, second, third))
                    score = 12.0 * unlocks + pressure + 0.2 * visible_third.bit_count()
                    triples[after_third] = (score, (first, second, third), after_third, visible_third)
        for _score, cards, next_remaining, next_available in sorted(
            triples.values(), key=lambda item: item[0], reverse=True
        )[:config.max_branches]:
            path.extend(("board", card) for card in cards)
            if visit(next_remaining, next_available):
                return True
            del path[-3:]
            if explored >= config.max_states or time.monotonic() >= deadline:
                return False
        return False

    solved = visit(all_remaining, board.initial_available)
    return SolveResult(
        solved, tuple(path) if solved else best, explored, None,
        time.monotonic() - started,
        "triple-batch route solved" if solved else "triple-batch budget exhausted or restricted search failed",
    )


def solve_counts(
    board: Board,
    config: DFSConfig = DFSConfig(),
    *,
    prefix_observer: Callable[[tuple[Action, ...], int, int], None] | None = None,
) -> SolveResult:
    """Exact no-tool DFS with one state per removed-tile set.

    Without parked cards, each type contributes ``removed_count % 3`` tray
    cards. Their order is irrelevant to capacity and terminal feasibility, so
    every ordering that reaches the same removed set can be merged safely.
    """
    started = time.monotonic()
    deadline = started + config.time_limit_seconds
    all_remaining = (1 << board.size) - 1
    counts = [0] * len(board.type_values)
    remaining_counts = [0] * len(board.type_values)
    for card_type in board.types:
        remaining_counts[board.type_indexes[card_type]] += 1
    initial_counts = tuple(remaining_counts)
    path: list[Action] = []
    best: tuple[Action, ...] = ()
    explored = 0
    restarts_run = 0

    def remove(remaining: int, available: int, card: int) -> tuple[int, int]:
        remaining &= ~(1 << card)
        available &= ~(1 << card)
        for dependent in board.dependents[card]:
            bit = 1 << dependent
            if remaining & bit and board.blocker_masks[dependent] & remaining == 0:
                available |= bit
        return remaining, available

    for restart in range(config.restarts):
        if explored >= config.max_states or time.monotonic() >= deadline:
            break
        restarts_run += 1
        rng = random.Random(config.seed + restart)
        restart_deadline = started + config.time_limit_seconds * (restart + 1) / config.restarts
        restart_state_limit = config.max_states * (restart + 1) // config.restarts
        seen: set[int] = set()
        path.clear()
        counts[:] = [0] * len(counts)
        remaining_counts[:] = initial_counts

        def visit(remaining: int, available: int, occupied: int) -> bool:
            nonlocal explored, best
            explored += 1
            if explored % 512 == 0 and (explored >= restart_state_limit or time.monotonic() >= restart_deadline):
                return False
            improved = len(path) > len(best)
            if improved:
                best = tuple(path)
            if prefix_observer is not None and (improved or explored % 512 == 0):
                prefix_observer(tuple(path), remaining, occupied)
            if remaining == 0:
                return occupied == 0
            if remaining in seen:
                return False
            seen.add(remaining)
            candidates: list[tuple[float, int, int]] = []
            for card in _iter_bits(available):
                type_index = board.type_indexes[board.types[card]]
                held = counts[type_index]
                if occupied >= TRAY_LIMIT and held != 2:
                    continue
                score = 100.0 if held == 2 else 50.0 if held else 0.0
                score += 30.0 if remaining_counts[type_index] >= 3 else 20.0 if remaining_counts[type_index] == 2 else 0.0
                if board.layers:
                    score += config.layer_weight * board.layers[card]
                if occupied >= 5 and held:
                    score += 40.0
                candidates.append((score, card, type_index))
            if config.seed + restart:
                rng.shuffle(candidates)
            candidates.sort(key=lambda item: item[0], reverse=True)
            for _score, card, type_index in candidates[:config.max_branches]:
                old = counts[type_index]
                counts[type_index] = (old + 1) % 3
                remaining_counts[type_index] -= 1
                next_occupied = occupied - 2 if old == 2 else occupied + 1
                next_remaining, next_available = remove(remaining, available, card)
                path.append(("board", card))
                if visit(next_remaining, next_available, next_occupied):
                    return True
                path.pop()
                counts[type_index] = old
                remaining_counts[type_index] += 1
                if explored >= restart_state_limit or time.monotonic() >= restart_deadline:
                    return False
            return False

        if visit(all_remaining, board.initial_available, 0):
            return SolveResult(
                True, tuple(path), explored, restart, time.monotonic() - started,
                "count-state DFS solved without tools",
            )
    return SolveResult(
        False, best, explored, None, time.monotonic() - started,
        f"count-state DFS budget exhausted after {restarts_run} restarts",
    )


def solve_hybrid(board: Board, config: DFSConfig = DFSConfig()) -> SolveResult:
    """Seed tool-aware DFS with a deep legal no-tool prefix, as in yangyang-hunter.

    Every candidate is replayed from the actual initial board; partial routes
    are retained only as search diagnostics and never presented as a solution.
    """
    started = time.monotonic()
    explored = 0
    best: tuple[Action, ...] = ()
    best_board_picks = 0
    attempts_run = 0
    for attempt in range(config.restarts):
        remaining_time = config.time_limit_seconds - (time.monotonic() - started)
        if remaining_time <= 0 or explored >= config.max_states:
            break
        attempts_run += 1
        attempt_time = min(remaining_time, config.time_limit_seconds / config.restarts)
        attempt_states = max(1, (config.max_states - explored) // (config.restarts - attempt))
        normal = solve_counts(board, DFSConfig(
            time_limit_seconds=attempt_time * 0.42,
            max_states=max(1, int(attempt_states * 0.42)),
            max_branches=max(1, config.max_branches - 2),
            restarts=1,
            seed=config.seed + attempt,
            order_jitter=config.order_jitter,
            layer_weight=config.layer_weight,
        ))
        explored += normal.explored_states
        if normal.solved:
            return SolveResult(
                True, normal.moves, explored, attempt,
                time.monotonic() - started, "hybrid no-tool prefix solved",
            )
        prefix = normal.moves
        prefix_state = replay(board, prefix)
        tool_time = max(0.01, attempt_time - normal.elapsed_seconds)
        tool = solve_dfs(board, DFSConfig(
            time_limit_seconds=tool_time,
            max_states=max(1, attempt_states - normal.explored_states),
            max_branches=config.max_branches,
            restarts=1,
            seed=config.seed + attempt,
            allow_move_out=config.allow_move_out,
            allow_revive=config.allow_revive,
            move_out_min_tray=config.move_out_min_tray,
            order_jitter=config.order_jitter,
            layer_weight=config.layer_weight,
        ), initial_state=prefix_state)
        explored += tool.explored_states
        combined = prefix + tool.moves
        board_picks = sum(action == "board" for action, _ in combined)
        if board_picks > best_board_picks:
            best, best_board_picks = combined, board_picks
        if tool.solved:
            final = replay(board, combined, allow_revive=config.allow_revive)
            if final.remaining != 0 or final.tray or any(final.parked):
                raise ValueError("拼接后的道具路线未通过完整回放")
            return SolveResult(
                True, combined, explored, attempt,
                time.monotonic() - started, "hybrid tool route solved and replayed",
            )
    return SolveResult(
        False, best, explored, None, time.monotonic() - started,
        f"hybrid budget exhausted after {attempts_run} attempts",
    )


class _MCTSNode:
    __slots__ = ("state", "parent", "action", "children", "untried", "visits", "value")

    def __init__(self, state: SearchState, parent: "_MCTSNode | None" = None, action: Action | None = None) -> None:
        self.state = state
        self.parent = parent
        self.action = action
        self.children: list[_MCTSNode] = []
        self.untried: list[Action] | None = None
        self.visits = 0
        self.value = 0.0


def _strip_moves(state: SearchState) -> SearchState:
    return SearchState(
        state.remaining, state.available, state.tray, state.parked,
        state.move_out_used, state.revive_used, (),
    )


def _mcts_actions(board: Board, state: SearchState, config: MCTSConfig, rng: random.Random) -> list[Action]:
    scored = _rollout_actions(
        board,
        state,
        SearchConfig(
            allow_move_out=config.allow_move_out,
            allow_revive=config.allow_revive,
        ),
        rng,
    )
    return [action for _score, action in sorted(scored, reverse=True)]


def _rollout_from(
    board: Board, state: SearchState, config: MCTSConfig, rng: random.Random
) -> tuple[bool, float, tuple[Action, ...]]:
    """Run a biased random playout and return its raw actionable sequence."""
    current = _strip_moves(state)
    actions_taken: list[Action] = []
    rollout_config = SearchConfig(
        allow_move_out=config.allow_move_out,
        allow_revive=config.allow_revive,
    )
    for _ in range(board.size + 10):
        if current.remaining == 0 and not current.tray and not any(current.parked):
            return True, 1.0, tuple(actions_taken)
        scored = _rollout_actions(board, current, rollout_config, rng)
        valid: list[tuple[float, Action]] = []
        for score, action in scored:
            if transition(board, current, action, allow_revive=config.allow_revive) is not None:
                valid.append((score, action))
        if not valid:
            break
        ceiling = max(score for score, _action in valid)
        weights = [max(0.02, 1.0 + (score - ceiling + 50.0) / 25.0) for score, _action in valid]
        action = rng.choices([action for _score, action in valid], weights=weights, k=1)[0]
        next_state = transition(board, current, action, allow_revive=config.allow_revive)
        if next_state is None:
            break
        actions_taken.append(action)
        current = _strip_moves(next_state)
    progress = (board.size - current.remaining.bit_count()) / board.size
    progress -= 0.01 * len(current.tray) + 0.002 * sum(len(slot) for slot in current.parked)
    return False, max(0.0, progress), tuple(actions_taken)


def _materialize_actions(board: Board, raw_actions: Iterable[Action], *, allow_revive: bool) -> tuple[Action, ...]:
    """Include the client-triggered revive marker after its overflowing pick."""
    state = SearchState((1 << board.size) - 1, board.initial_available, (), ((), (), ()), False, False, ())
    output: list[Action] = []
    for action in raw_actions:
        next_state = transition(board, state, action, allow_revive=allow_revive)
        if next_state is None:
            raise ValueError("MCTS returned an invalid action")
        output.extend(next_state.moves[len(state.moves):])
        state = next_state
    return tuple(output)


def solve_mcts(board: Board, config: MCTSConfig = MCTSConfig()) -> SolveResult:
    """Single-player MCTS with exact game-state transitions and biased rollouts."""
    started = time.monotonic()
    rng = random.Random(config.seed)
    root_state = SearchState((1 << board.size) - 1, board.initial_available, (), ((), (), ()), False, False, ())
    root = _MCTSNode(root_state)
    explored = 0
    for _iteration in range(config.iterations):
        if time.monotonic() - started >= config.time_limit_seconds:
            break
        node = root
        prefix: list[Action] = []
        # Selection and one expansion. UCT is maximized because the reward is
        # a single-player completion/progress value.
        while True:
            if node.untried is None:
                node.untried = _mcts_actions(board, node.state, config, rng)
            if node.untried:
                action = node.untried.pop(0)
                child_state = transition(board, node.state, action, allow_revive=config.allow_revive)
                if child_state is None:
                    continue
                child = _MCTSNode(_strip_moves(child_state), node, action)
                node.children.append(child)
                node = child
                prefix.append(action)
                break
            if not node.children:
                break
            logarithm = max(1.0, float(node.visits))
            node = max(
                node.children,
                key=lambda child: (
                    child.value / child.visits
                    + config.exploration * math.sqrt(math.log(logarithm + 1.0) / child.visits)
                ),
            )
            prefix.append(node.action)  # type: ignore[arg-type]

        won, reward, suffix = _rollout_from(board, node.state, config, rng)
        explored += 1
        if won:
            raw = tuple(prefix) + suffix
            plan = _materialize_actions(board, raw, allow_revive=config.allow_revive)
            final = replay(board, plan, allow_revive=config.allow_revive)
            if final.remaining == 0 and not final.tray and not any(final.parked):
                return SolveResult(True, plan, explored, 0, time.monotonic() - started, "SP-MCTS solved")
        while node is not None:
            node.visits += 1
            node.value += reward
            node = node.parent
    return SolveResult(False, (), explored, None, time.monotonic() - started, "SP-MCTS limit reached")


def replay(board: Board, moves: Iterable[Action], *, allow_revive: bool = True) -> SearchState:
    """Independently validate returned actions against client-equivalent rules."""
    state = SearchState((1 << board.size) - 1, board.initial_available, (), ((), (), ()), False, False, ())
    for action in moves:
        if action[0] == "revive_move_out":
            continue  # emitted with the preceding overflowing pick, not clicked separately
        next_state = transition(board, state, action, allow_revive=allow_revive)
        if next_state is None:
            raise ValueError("plan overflows the seven-slot tray")
        state = next_state
    return state


def result_to_dict(board: Board, result: SolveResult) -> dict[str, object]:
    final_state = replay(board, result.moves) if result.solved else None
    actions = []
    for step, (kind, value) in enumerate(result.moves, start=1):
        record: dict[str, object] = {"step": step, "action": kind}
        if kind == "board":
            record.update({
                "tile_index": value, "tile_id": board.ids[value], "type": board.types[value],
                "face_resource": f"block/daily/block_{board.types[value]}",
            })
        elif kind == "park":
            record["temporary_slot"] = value + 1
        actions.append(record)
    return {
        "solver": "exact tray/tool state search",
        "solved": result.solved, "reason": result.reason,
        "explored_states": result.explored_states, "restart": result.restart,
        "elapsed_seconds": round(result.elapsed_seconds, 3), "action_count": len(result.moves),
        "actions": actions,
        "replay": None if final_state is None else {
            "remaining_tiles": final_state.remaining.bit_count(), "tray_cards": len(final_state.tray),
            "parked_cards": sum(len(slot) for slot in final_state.parked),
            "move_out_used": final_state.move_out_used, "revive_used": final_state.revive_used,
        },
    }
