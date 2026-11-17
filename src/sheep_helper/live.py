"""Capture-to-plan workflow and explicit, player-confirmed guide progress.

No function in this module sends input to the game. A recovered seed is only
accepted for its matching map, and an incomplete solver path is never offered
as a playable plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import queue
import threading
import time
from typing import Callable
from urllib.request import urlopen

from .map_format import decode_map
from .capture_state import safe_game_route, valid_map_ids
from .pipeline import validate_seed_record, write_session
from .session import ManualPlaySession
from .compact_solver import backend_path, solve_compact
from .solver import Action, Board, DFSConfig, SolveResult, replay, result_to_dict, solve_hybrid, DEFAULT_SOLVE_TIMEOUT


@dataclass(frozen=True)
class CapturedGame:
    seed_path: Path
    map_path: Path
    map_id: str
    level_key: int
    tile_count: int
    source_path: str | None = None


@dataclass(frozen=True)
class PreparedGame:
    capture: CapturedGame
    layout_path: Path
    coverage_path: Path
    cards_path: Path
    solution_path: Path


def fetch_missing_map(capture_dir: Path, *, not_before: float, timeout: float = 8.0) -> Path | None:
    """Fetch the public map named by this session when the client used its cache.

    The address is fixed to the game's static asset host; an untrusted packet
    can supply only a validated 32-character hex filename, never a URL.
    """
    seed_dir = capture_dir / "seeds"
    if not seed_dir.is_dir():
        return None
    seeds = sorted(seed_dir.glob("recovered-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for seed_path in seeds:
        if seed_path.stat().st_mtime < not_before:
            continue
        try:
            record = json.loads(seed_path.read_text(encoding="utf-8"))
            ids = valid_map_ids(record.get("map_md5"))
            if not ids:
                continue
            map_id = ids[1] if len(ids) > 1 else ids[0]
            validate_seed_record(record, map_id)
        except (OSError, ValueError, TypeError):
            continue
        destination = capture_dir / "maps" / f"{map_id}.map"
        if destination.is_file() or (capture_dir / f"{map_id}.map").is_file():
            return None
        url = f"https://cat-match-static.easygame2021.com/maps/{map_id}.map"
        with urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError(f"地图资源 HTTP {response.status}")
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("地图资源异常过大")
        layout = decode_map(raw)
        if int(layout["tile_count"]) < 30:
            raise ValueError("补取的资源不是目标第二关地图")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".map.tmp")
        try:
            temporary.write_bytes(raw)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
    return None


def find_captured_game(
    capture_dir: Path, *, not_before: float = 0.0, min_tiles: int = 30,
) -> CapturedGame | None:
    """Choose level two from a two-stage start response, never level one."""
    seed_dir = capture_dir / "seeds"
    if not seed_dir.is_dir():
        return None
    seeds = sorted(seed_dir.glob("recovered-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for seed_path in seeds:
        if seed_path.stat().st_mtime < not_before:
            continue
        try:
            record = json.loads(seed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        map_ids = record.get("map_md5")
        if isinstance(map_ids, str):
            map_ids = [map_ids]
        if not isinstance(map_ids, list):
            continue
        # The server orders its maps by stage.  A tile-count heuristic alone
        # would silently guide level one if its layout grows past min_tiles.
        target_ids = map_ids[1:2] if len(map_ids) > 1 else map_ids
        choices: list[CapturedGame] = []
        for map_id in target_ids:
            if not isinstance(map_id, str) or len(map_id) != 32 or any(ch not in "0123456789abcdefABCDEF" for ch in map_id):
                continue
            map_id = map_id.lower()
            try:
                validate_seed_record(record, map_id)
            except ValueError:
                continue
            map_path = capture_dir / "maps" / f"{map_id}.map"
            if not map_path.is_file():
                map_path = capture_dir / f"{map_id}.map"
            if not map_path.is_file():
                continue
            try:
                layout = decode_map(map_path.read_bytes())
            except (OSError, ValueError):
                continue
            count = int(layout["tile_count"])
            if count >= min_tiles:
                choices.append(CapturedGame(seed_path, map_path, map_id, int(layout["level_key"]), count,
                                            safe_game_route(str(record.get("source_path") or ""))))
        if choices:
            return choices[0]
    return None


def prepare_captured_game(capture: CapturedGame, capture_dir: Path) -> PreparedGame:
    session_dir = capture_dir / f"session-{capture.seed_path.stem.removeprefix('recovered-')}" / "verified"
    layout_path, coverage_path, cards_path = write_session(capture.map_path, capture.seed_path, session_dir)
    return PreparedGame(
        capture, layout_path, coverage_path, cards_path,
        session_dir / f"{capture.map_id}.guide-solution.json",
    )


def _board_actions(board: Board, record: dict[str, object]) -> tuple[Action, ...]:
    if record.get("solved") is not True:
        raise ValueError("未找到完整通关路线，不能启动逐步标记")
    raw = record.get("actions")
    if not isinstance(raw, list):
        raise ValueError("路线缺少 actions")
    moves: list[Action] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("路线动作格式无效")
        kind = item.get("action")
        if kind == "board":
            index = item.get("tile_index")
            if type(index) is not int or not 0 <= index < board.size:
                raise ValueError("路线包含无效 tile_index")
            if item.get("tile_id") != board.ids[index] or item.get("type") != board.types[index]:
                raise ValueError("路线牌面与当前局不一致")
            moves.append(("board", index))
        elif kind == "park":
            slot = item.get("temporary_slot")
            if type(slot) is not int or not 1 <= slot <= 3:
                raise ValueError("路线包含无效临时槽位")
            moves.append(("park", slot - 1))
        elif kind in ("move_out", "revive_move_out"):
            moves.append((kind, -1))
        else:
            raise ValueError(f"路线包含未知动作：{kind}")
    state = replay(board, moves)
    if state.moves != tuple(moves):
        raise ValueError("路线中的复活标记与实际触发顺序不一致")
    if state.remaining or state.tray or any(state.parked):
        raise ValueError("路线未通过当前局的完整回放")
    return tuple(moves)


def load_verified_plan(board: Board, path: Path) -> tuple[Action, ...]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("路线文件必须是 JSON 对象")
    return _board_actions(board, record)


def solve_prepared_game(
    prepared: PreparedGame, *, timeout: float = DEFAULT_SOLVE_TIMEOUT,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[Board, tuple[Action, ...]]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("求解最长时间必须大于零")
    cards = json.loads(prepared.cards_path.read_text(encoding="utf-8"))
    if any(int(tile.get("meta_type", 0)) == 2 for tile in cards["tiles"]):
        raise ValueError("本关含消除后会变换牌面的特殊砖块；静态路线不适用，请换一局")
    board = Board.from_files(prepared.cards_path, prepared.coverage_path)
    if prepared.solution_path.is_file():
        return board, load_verified_plan(board, prepared.solution_path)
    result = _solve_portfolio(board, timeout, prepared.solution_path.parent / ".solver-runtime",
                              progress, cancelled)
    if cancelled is not None and cancelled():
        raise RuntimeError("求解已取消")
    record = result_to_dict(board, result)
    record["map_md5"] = prepared.capture.map_id
    record["map_seed"] = json.loads(prepared.capture.seed_path.read_text(encoding="utf-8"))["map_seed"]
    moves = _board_actions(board, record)
    prepared.solution_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return board, moves


def _solve_portfolio(
    board: Board, timeout: float, runtime_dir: Path,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> SolveResult:
    """Race complementary searches under one deadline; stop the losing worker.

    Some layouts favor the original prefix heuristic, others need many compact
    restarts. Both retain the full time budget and every winner is replayed.
    """
    started = time.monotonic()
    deadline = started + timeout
    stop = threading.Event()
    results: queue.Queue[tuple[str, SolveResult | None, Exception | None]] = queue.Queue()

    def should_stop() -> bool:
        return stop.is_set() or (cancelled is not None and cancelled())

    def legacy() -> SolveResult:
        attempt = total_states = 0
        while not should_stop():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if progress is not None:
                progress(f"正在求完整路线：第 {attempt + 1} 组搜索，剩余最多 {remaining:.0f} 秒")
            candidate = solve_hybrid(board, DFSConfig(
                time_limit_seconds=min(remaining, 20.0), max_states=3_500_000,
                max_branches=8, restarts=1, seed=attempt, cancelled=should_stop,
            ))
            total_states += candidate.explored_states
            if candidate.solved:
                return candidate
            attempt += 1
        return SolveResult(False, (), total_states, None, time.monotonic() - started,
                           f"{attempt} search groups")

    def worker(name: str, search: Callable[[], SolveResult | None]) -> None:
        try:
            result = search()
            if result is not None and result.solved:
                final = replay(board, result.moves)
                if final.remaining or final.tray or any(final.parked) or final.moves != result.moves:
                    raise ValueError("搜索路线未通过独立完整回放")
            results.put((name, result, None))
        except Exception as error:
            results.put((name, None, error))

    binary = backend_path(board) if timeout >= 1 else None
    searches: list[tuple[str, Callable[[], SolveResult | None]]] = [("legacy", legacy)]
    if binary is not None:
        if progress is not None:
            progress("正在并行搜索：原算法与多前缀快速搜索，共用求解时限")
        searches.append(("compact", lambda: solve_compact(
            board, max(0.001, deadline - time.monotonic()), binary, runtime_dir, should_stop)))
    total_states = 0
    errors: list[str] = []
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sheep-search") if binary is not None else None
    try:
        for name, search in searches:
            if pool is not None:
                pool.submit(worker, name, search)
            else:
                worker(name, search)
        pending = len(searches)
        while pending:
            if cancelled is not None and cancelled():
                raise RuntimeError("求解已取消")
            try:
                name, result, error = results.get(timeout=0.05)
            except queue.Empty:
                # Native process is allowed a short output/exit grace period.
                if time.monotonic() > deadline + 0.6:
                    break
                continue
            pending -= 1
            if error is not None:
                errors.append(f"{name}: {error}")
                if progress is not None and name == "compact":
                    progress(f"快速搜索不可用，继续原算法：{error}")
            elif result is not None:
                total_states += result.explored_states
                if result.solved:
                    return result
    finally:
        stop.set()
        if pool is not None:
            pool.shutdown(wait=True)
    if cancelled is not None and cancelled():
        raise RuntimeError("求解已取消")
    detail = f"；搜索异常：{' / '.join(errors)}" if errors else ""
    raise RuntimeError(
        f"{timeout:g} 秒上限内未找到完整路线（{total_states} 状态{detail}）。"
        "这不等于证明无解；可提高求解时限后载入最近抓包重试（牌局须保持不变）。"
        "若牌局已变，建议重开关卡后再试。"
    )


def _plan_digest(plan: tuple[Action, ...]) -> str:
    return hashlib.sha256(json.dumps(plan, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass
class GuidedPlay:
    board: Board
    plan: tuple[Action, ...]
    progress_path: Path | None = None
    session: ManualPlaySession = field(init=False)
    confirmed: int = 0

    def __post_init__(self) -> None:
        final = replay(self.board, self.plan)
        if final.remaining or final.tray or any(final.parked):
            raise ValueError("逐步指引只接受完整通关路线")
        self.session = ManualPlaySession(self.board)
        self.session.plan = self.plan

    @property
    def next_action(self) -> Action | None:
        return self.session.next_action()

    @property
    def finished(self) -> bool:
        state = self.session.state
        return state.remaining == 0 and not state.tray and not any(state.parked)

    def _advance(self, *, save: bool) -> Action:
        action = self.next_action
        if action is None:
            raise ValueError("没有待确认的操作")
        self.session.apply(action)
        self.confirmed += 1
        if save:
            self.save_progress()
        return action

    def confirm_next(self) -> Action:
        return self._advance(save=True)

    def save_progress(self) -> None:
        if self.progress_path is None:
            return
        payload = {
            "format": "sheep-helper-guided-progress/v1",
            "plan_sha256": _plan_digest(self.plan),
            "confirmed": self.confirmed,
            "remaining_tiles": self.session.state.remaining.bit_count(),
            "updated_at": time.time(),
        }
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.progress_path.with_suffix(self.progress_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.progress_path)

    def resume(self) -> None:
        if self.progress_path is None:
            raise ValueError("未设置进度文件")
        payload = json.loads(self.progress_path.read_text(encoding="utf-8"))
        if payload.get("plan_sha256") != _plan_digest(self.plan):
            raise ValueError("进度文件与当前局路线不一致")
        count = payload.get("confirmed")
        if type(count) is not int or count < 0 or count > len(self.plan):
            raise ValueError("进度文件中的步数无效")
        for _ in range(count):
            self._advance(save=False)
        if self.session.state.remaining.bit_count() != payload.get("remaining_tiles"):
            raise ValueError("进度文件校验失败")
