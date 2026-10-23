"""Double-clickable entry point for the Windows guided-play application."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tkinter as tk
from tkinter import messagebox

from sheep_helper import guide_ui
from sheep_helper.system_proxy import watch_owner


def default_capture_dir() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if executable.parent.name.lower() == "dist":
            project_capture = executable.parent.parent / "captured-layouts"
            if project_capture.is_dir():
                return project_capture
        sibling_capture = executable.parent / "captured-layouts"
        if sibling_capture.is_dir():
            return sibling_capture
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "SheepHelper" / "captured-layouts"


def self_test(output: Path, *, probe_capture: bool = False) -> bool:
    addon_dir = Path(guide_ui.__file__).resolve().parent
    files = ("__init__.py", "extractor.py", "game_capture_addon.py", "seed_recovery_addon.py",
             "redaction.py", "capture_state.py", "match_data.py")
    present = {name: (addon_dir / name).is_file() for name in files}
    root = tk.Tk()
    root.withdraw()
    try:
        root.update_idletasks()
    finally:
        root.destroy()
    result = {
        "packaged": bool(getattr(sys, "frozen", False)),
        "addon_sources": present,
        "mitmdump": shutil.which("mitmdump"),
        "capture_dir": str(default_capture_dir()),
    }
    if probe_capture:
        process = None
        try:
            if guide_ui._port_running(18889):
                raise RuntimeError("测试端口 18889 已被占用")
            with tempfile.TemporaryDirectory(prefix="sheep-helper-capture-test-") as temporary:
                try:
                    process = guide_ui._start_capture(Path(temporary), port=18889)
                    if process is None:
                        raise RuntimeError("测试抓包进程未启动")
                    time.sleep(0.5)
                    result["capture_probe"] = process.poll() is None
                finally:
                    if process is not None and process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
        except (OSError, RuntimeError, ValueError) as error:
            result["capture_probe"] = False
            result["capture_error"] = str(error)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return all(present.values()) and (not probe_capture or result["capture_probe"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="羊了个羊：星球 · 手动通关指引")
    parser.add_argument("--capture-dir", type=Path, default=default_capture_dir())
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--no-start-capture", action="store_true")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--self-test", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--probe-capture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--proxy-watchdog", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--owner-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.proxy_watchdog is not None:
        if args.owner_pid is None or args.owner_pid <= 0:
            return 2
        watch_owner(args.proxy_watchdog, args.owner_pid)
        return 0
    try:
        if args.self_test is not None:
            return 0 if self_test(args.self_test, probe_capture=args.probe_capture) else 1
        if args.smoke:
            app = guide_ui.GuideApp(capture_dir=args.capture_dir, start_capture=not args.no_start_capture)
            app.root.after(800, app.close)
            app.run()
            return 0
        guide_ui.run_guide(
            capture_dir=args.capture_dir,
            timeout=args.timeout,
            latest=args.latest,
            start_capture=not args.no_start_capture,
        )
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showerror("羊了个羊助手启动失败", str(error), parent=root)
        finally:
            root.destroy()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
