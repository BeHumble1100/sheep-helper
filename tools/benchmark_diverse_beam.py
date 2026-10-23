"""Experimental diversity-preserving no-tool beam search on a decoded board.

This is deliberately separate from the production solver until a same-board
benchmark shows that it improves on the count-state DFS baseline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from sheep_helper.solver import Board, SolveResult, _iter_bits, replay, result_to_dict


class Node:
    __slots__ = ("remaining", "available", "counts", "visible_counts", "occupied", "parent", "card", "score")

    def __init__(
        self, remaining: int, available: int, counts: tuple[int, ...],
        visible_counts: tuple[int, ...], occupied: int,
        parent: Node | None, card: int, score: float = 0.0,
    ) -> None:
        self.remaining = remaining
        self.available = available
        self.counts = counts
        self.visible_counts = visible_counts
        self.occupied = occupied
        self.parent = parent
        self.card = card
        self.score = score


def actions_for(node: Node) -> tuple[tuple[str, int], ...]:
    cards = []
    while node.parent is not None:
        cards.append(("board", node.card))
        node = node.parent
    cards.reverse()
    return tuple(cards)


def child(board: Board, node: Node, card: int) -> Node:
    bit = 1 << card
    remaining = node.remaining & ~bit
    available = node.available & ~bit
    visible = list(node.visible_counts)
    kind = board.type_indexes[board.types[card]]
    visible[kind] -= 1
    for dependent in board.dependents[card]:
        dependent_bit = 1 << dependent
        if remaining & dependent_bit and not board.blocker_masks[dependent] & remaining:
            available |= dependent_bit
            visible[board.type_indexes[board.types[dependent]]] += 1
    counts = list(node.counts)
    held = counts[kind]
    counts[kind] = (held + 1) % 3
    occupied = node.occupied - 2 if held == 2 else node.occupied + 1
    ready = sum(count for count, held in zip(visible, counts) if held == 2)
    supported = sum(count for count, held in zip(visible, counts) if held == 1)
    mobility = ready if occupied == 6 else available.bit_count()
    # A lower tray burden is valuable, but exposed matching cards and mobility
    # can justify carrying incomplete triples for a while.
    score = (
        9.0 * ready + 2.0 * supported + 1.0 * available.bit_count()
        - 6.0 * occupied + 2.0 * mobility
    )
    return Node(remaining, available, tuple(counts), tuple(visible),
                occupied, node, card, score)


def solve(board: Board, seconds: float, width: int) -> SolveResult:
    started = time.monotonic()
    visible = [0] * len(board.type_values)
    for card in _iter_bits(board.initial_available):
        visible[board.type_indexes[board.types[card]]] += 1
    root = Node((1 << board.size) - 1, board.initial_available,
                (0,) * len(board.type_values), tuple(visible), 0, None, -1)
    frontier = [root]
    best = root
    explored = 0
    for depth in range(1, board.size + 1):
        if time.monotonic() - started >= seconds:
            break
        generated: dict[int, Node] = {}
        for node in frontier:
            for card in _iter_bits(node.available):
                held = node.counts[board.type_indexes[board.types[card]]]
                if node.occupied == 6 and held != 2:
                    continue
                next_node = child(board, node, card)
                explored += 1
                if next_node.remaining == 0:
                    moves = actions_for(next_node)
                    if replay(board, moves).tray:
                        raise ValueError("beam route leaves cards in the tray")
                    return SolveResult(True, moves, explored, None,
                                       time.monotonic() - started, "diverse count beam solved")
                if next_node.occupied == 6 and not any(
                    count for count, held in zip(next_node.visible_counts, next_node.counts)
                    if held == 2
                ):
                    continue
                previous = generated.get(next_node.remaining)
                if previous is None or next_node.score > previous.score:
                    generated[next_node.remaining] = next_node
            if time.monotonic() - started >= seconds:
                break
        if not generated:
            break
        ranked = sorted(generated.values(), key=lambda node: node.score, reverse=True)
        frontier = []
        selected: set[int] = set()
        signatures: set[tuple[int, ...]] = set()
        for node in ranked:
            if len(frontier) >= width // 2:
                break
            if node.counts in signatures:
                continue
            signatures.add(node.counts)
            selected.add(node.remaining)
            frontier.append(node)
        for node in ranked:
            if len(frontier) >= width:
                break
            if node.remaining not in selected:
                selected.add(node.remaining)
                frontier.append(node)
        if depth > board.size - best.remaining.bit_count():
            best = frontier[0]
        if depth % 20 == 0:
            print(f"depth={depth} beam={len(frontier)} generated={len(generated)} "
                  f"explored={explored} elapsed={time.monotonic()-started:.1f}", flush=True)
    return SolveResult(False, actions_for(best), explored, None,
                       time.monotonic() - started, "diverse count beam budget exhausted")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--width", type=int, default=1_000)
    args = parser.parse_args()
    board = Board.from_files(args.cards, args.coverage)
    result = solve(board, args.seconds, args.width)
    record = result_to_dict(board, result)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"solved={result.solved} board_picks={len(result.moves)} "
          f"states={result.explored_states} seconds={result.elapsed_seconds:.2f}", flush=True)


if __name__ == "__main__":
    main()
