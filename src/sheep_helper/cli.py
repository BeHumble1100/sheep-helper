from __future__ import annotations

import argparse
import json
from pathlib import Path

from .card_types import restore_cards
from .extractor import extract_candidates
from .har import iter_har_payloads
from .map_format import decode_map
from .pipeline import map_id_from_path, validate_seed_record, write_session
from .session import ManualPlaySession
from .solver import DFSConfig, Board, MCTSConfig, SearchConfig, result_to_dict, solve, solve_batches, solve_counts, solve_dfs, solve_hybrid, solve_mcts


def _all_candidates(har_path: Path):
    results = []
    for url, payload in iter_har_payloads(har_path):
        for candidate in extract_candidates(payload):
            results.append((url, candidate))
    return sorted(results, key=lambda pair: (-pair[1].score, -len(pair[1].tiles), pair[0], pair[1].path))


def inspect(har_path: Path) -> int:
    candidates = _all_candidates(har_path)
    if not candidates:
        print("未找到疑似牌阵的 JSON 响应。")
        return 1
    for index, (url, candidate) in enumerate(candidates):
        print(f"[{index}] score={candidate.score} tiles={len(candidate.tiles)} types={len({tile.type for tile in candidate.tiles})}")
        print(f"    url={url}\n    path={candidate.path}\n    slot={candidate.slot}")
    return 0


def export(har_path: Path, index: int, output: Path) -> int:
    candidates = _all_candidates(har_path)
    if not 0 <= index < len(candidates):
        raise ValueError(f"candidate 必须在 0 到 {len(candidates) - 1} 之间")
    url, candidate = candidates[index]
    record = {"source": "har-import", "source_url": url, "board": candidate.to_dict()}
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出脱敏布局：{output}")
    return 0


def capture_command(process: str, output_dir: Path, recover_seed: bool) -> int:
    addon = Path(__file__).with_name("game_capture_addon.py").resolve()
    print("以下命令仅供用户在确认本机调试证书后手动运行：")
    command = f'mitmdump --mode local:{process} -s "{addon}" --set game_capture_dir="{output_dir.resolve()}"'
    if recover_seed:
        seed_addon = Path(__file__).with_name("seed_recovery_addon.py").resolve()
        command += f' -s "{seed_addon}" --set seed_recovery_dir="{output_dir.resolve()}"'
    print(command)
    print("默认仅保存公开 .map 资产和脱敏的游戏开始元数据。")
    if recover_seed:
        print("已启用一次性本地种子恢复探针：它会临时改写本地收到的种子字段，并在转发前恢复原请求；不保存 Cookie、token、密钥或加密流。")
    return 0


def decode_map_file(source: Path, output: Path) -> int:
    layout = decode_map(source.read_bytes())
    layout["source_map_md5"] = map_id_from_path(source)
    output.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tiles={layout['tile_count']} level_key={layout['level_key']} output={output}")
    return 0


def restore_cards_file(layout_file: Path, seed_file: Path, output: Path) -> int:
    layout = json.loads(layout_file.read_text(encoding="utf-8"))
    seed_record = json.loads(seed_file.read_text(encoding="utf-8"))
    map_id = layout.get("source_map_md5") or map_id_from_path(layout_file)
    layout["source_map_md5"] = map_id
    seed = validate_seed_record(seed_record, map_id)
    cards = restore_cards(layout, seed)
    output.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tiles={cards['tile_count']} output={output}")
    return 0


def solve_layout(
    cards: Path,
    coverage: Path,
    output: Path,
    beam_width: int,
    restarts: int,
    time_limit: float,
    seed: int,
    allow_move_out: bool,
    allow_revive: bool,
    rollout_attempts: int,
    algorithm: str,
    mcts_iterations: int,
    dfs_max_states: int,
    dfs_max_branches: int,
    dfs_layer_weight: float,
    move_out_min_tray: int,
) -> int:
    """Solve a captured map entirely locally; it never sends game actions."""
    board = Board.from_files(cards, coverage)
    if algorithm == "mcts":
        result = solve_mcts(board, MCTSConfig(
            iterations=mcts_iterations,
            time_limit_seconds=time_limit,
            seed=seed,
            allow_move_out=allow_move_out,
            allow_revive=allow_revive,
        ))
    elif algorithm in ("dfs", "batch", "counts", "hybrid"):
        config = DFSConfig(
            time_limit_seconds=time_limit,
            max_states=dfs_max_states,
            max_branches=dfs_max_branches,
            restarts=restarts,
            seed=seed,
            allow_move_out=allow_move_out,
            allow_revive=allow_revive,
            layer_weight=dfs_layer_weight,
            move_out_min_tray=move_out_min_tray,
        )
        result = {
            "dfs": solve_dfs,
            "batch": solve_batches,
            "counts": solve_counts,
            "hybrid": solve_hybrid,
        }[algorithm](board, config)
    else:
        result = solve(board, SearchConfig(
            beam_width=beam_width,
            restarts=restarts,
            time_limit_seconds=time_limit,
            seed=seed,
            allow_move_out=allow_move_out,
            allow_revive=allow_revive,
            rollout_attempts=rollout_attempts,
        ))
    record = result_to_dict(board, result)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"solved={result.solved} moves={len(result.moves)} "
        f"states={result.explored_states} elapsed={result.elapsed_seconds:.2f}s"
    )
    return 0 if result.solved else 2


def advise_layout(cards: Path, coverage: Path, time_limit: float, max_states: int, branches: int) -> int:
    """Plan locally, then emit the next manual action and visible cards."""
    session = ManualPlaySession(Board.from_files(cards, coverage))
    result = session.solve(DFSConfig(
        time_limit_seconds=time_limit,
        max_states=max_states,
        max_branches=branches,
    ))
    print(json.dumps(session.public_state(), ensure_ascii=False, indent=2))
    return 0 if result.solved else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="本地、脱敏优先的牌阵提取工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("har", type=Path)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("har", type=Path)
    export_parser.add_argument("--candidate", type=int, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    capture_parser = subparsers.add_parser("capture-command")
    capture_parser.add_argument("--process", required=True)
    capture_parser.add_argument("--output-dir", type=Path, default=Path("captured-layouts"))
    capture_parser.add_argument("--recover-seed", action="store_true", help="显式启用一次性本地加密种子恢复探针")
    decode_parser = subparsers.add_parser("decode-map", help="解包已捕获的 .map 资产")
    decode_parser.add_argument("--source", required=True, type=Path)
    decode_parser.add_argument("--output", required=True, type=Path)
    restore_parser = subparsers.add_parser("restore-cards", help="由解包地图和恢复的种子还原牌面")
    restore_parser.add_argument("--layout", required=True, type=Path)
    restore_parser.add_argument("--seed-file", required=True, type=Path)
    restore_parser.add_argument("--output", required=True, type=Path)
    prepare_parser = subparsers.add_parser("prepare-session", help="校验地图与种子并生成布局、遮挡图和逐张牌面")
    prepare_parser.add_argument("--map", required=True, type=Path)
    prepare_parser.add_argument("--seed-file", required=True, type=Path)
    prepare_parser.add_argument("--output-dir", required=True, type=Path)
    prepare_parser.add_argument("--observed", type=Path, help="可选的实测牌面 JSON：{tile_id: type}")
    solve_parser = subparsers.add_parser("solve")
    solve_parser.add_argument("--cards", required=True, type=Path)
    solve_parser.add_argument("--coverage", required=True, type=Path)
    solve_parser.add_argument("--output", required=True, type=Path)
    solve_parser.add_argument("--beam-width", type=int, default=4_000)
    solve_parser.add_argument("--restarts", type=int, default=8)
    solve_parser.add_argument("--time-limit", type=float, default=45.0)
    solve_parser.add_argument("--seed", type=int, default=20261021)
    solve_parser.add_argument("--no-move-out", action="store_true", help="不使用一次移出三张道具")
    solve_parser.add_argument("--no-revive", action="store_true", help="不使用第七格后的复活移出机会")
    solve_parser.add_argument("--rollout-attempts", type=int, default=1_500)
    solve_parser.add_argument("--algorithm", choices=("beam", "dfs", "mcts", "batch", "counts", "hybrid"), default="dfs")
    solve_parser.add_argument("--mcts-iterations", type=int, default=250_000)
    solve_parser.add_argument("--dfs-max-states", type=int, default=10_000_000)
    solve_parser.add_argument("--dfs-max-branches", type=int, default=7)
    solve_parser.add_argument("--dfs-layer-weight", type=float, default=2.0)
    solve_parser.add_argument("--move-out-min-tray", type=int, choices=range(1, 7), default=5)
    advise_parser = subparsers.add_parser("advise", help="生成只供玩家手动执行的下一步建议")
    advise_parser.add_argument("--cards", required=True, type=Path)
    advise_parser.add_argument("--coverage", required=True, type=Path)
    advise_parser.add_argument("--time-limit", type=float, default=60.0)
    advise_parser.add_argument("--max-states", type=int, default=5_000_000)
    advise_parser.add_argument("--branches", type=int, default=7)
    guide_parser = subparsers.add_parser("guide", help="抓取新局、限时求解并在游戏窗口标记下一步（不自动点击）")
    guide_parser.add_argument("--capture-dir", type=Path, default=Path("captured-layouts"))
    guide_parser.add_argument("--timeout", type=float, default=None, help="覆盖已保存的求解时限；未设置过时默认 20 秒")
    guide_parser.add_argument("--latest", action="store_true", help="使用最近已捕获的局；默认仅等待启动后的新局")
    guide_parser.add_argument("--no-start-capture", action="store_true", help="使用已运行的本机抓包代理")
    guide_parser.add_argument("--resume", action="store_true", help="按相同路线的进度文件恢复；须先核对游戏画面")
    guide_parser.add_argument("--cards", type=Path, help="离线预览：与 --coverage、--solution 同时提供")
    guide_parser.add_argument("--coverage", type=Path)
    guide_parser.add_argument("--solution", type=Path)
    args = parser.parse_args()
    if args.command == "inspect":
        return inspect(args.har)
    if args.command == "export":
        return export(args.har, args.candidate, args.output)
    if args.command == "decode-map":
        return decode_map_file(args.source, args.output)
    if args.command == "restore-cards":
        return restore_cards_file(args.layout, args.seed_file, args.output)
    if args.command == "prepare-session":
        layout_path, coverage_path, cards_path = write_session(
            args.map, args.seed_file, args.output_dir, args.observed
        )
        print(f"已生成：{layout_path} {coverage_path} {cards_path}")
        return 0
    if args.command == "solve":
        return solve_layout(
            args.cards,
            args.coverage,
            args.output,
            args.beam_width,
            args.restarts,
            args.time_limit,
            args.seed,
            not args.no_move_out,
            not args.no_revive,
            args.rollout_attempts,
            args.algorithm,
            args.mcts_iterations,
            args.dfs_max_states,
            args.dfs_max_branches,
            args.dfs_layer_weight,
            args.move_out_min_tray,
        )
    if args.command == "advise":
        return advise_layout(
            args.cards, args.coverage, args.time_limit, args.max_states, args.branches
        )
    if args.command == "guide":
        if args.timeout is not None and not 1 <= args.timeout <= 3600:
            parser.error("--timeout 必须为 1～3600 秒")
        from .guide_ui import run_guide
        run_guide(
            capture_dir=args.capture_dir,
            cards=args.cards,
            coverage=args.coverage,
            solution=args.solution,
            latest=args.latest,
            start_capture=not args.no_start_capture,
            resume=args.resume,
            timeout=args.timeout,
        )
        return 0
    return capture_command(args.process, args.output_dir, args.recover_seed)


if __name__ == "__main__":
    raise SystemExit(main())
