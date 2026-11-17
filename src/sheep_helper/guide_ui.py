"""Windows click-through overlay for a player-confirmed Sheep Planet plan."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from PIL import ImageGrab

from .card_faces import board_region, face_name
from .background import BackgroundFrames, post_click
from .capture_state import CAPTURE_DONE_MARKER
from .capture_runtime import prepare_capture_runtime
from .guide_settings import load_timeout, save_timeout, validate_timeout
from .live import GuidedPlay, fetch_missing_map, find_captured_game, load_verified_plan, prepare_captured_game, solve_prepared_game
from .solver import Board, replay, DEFAULT_SOLVE_TIMEOUT
from .system_proxy import CaptureProxy, recover_stale_proxy, _read_proxy, SUPPORTED_UPSTREAMS
from .vision import (
    Alignment, find_alignment, find_daily_alignment, find_exposed_card_center,
    is_exposed_card, visibly_changed, find_parked_cards,
)


GAME_TITLE = "羊了个羊：星球"
TRANSPARENT = "#010203"
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 7890
CAPTURE_PORT = 18888
BG = "#0b1424"
PANEL = "#15243a"
MUTED = "#91a5bf"
TEXT = "#edf5ff"
ACCENT = "#55e5bd"


def tile_center(
    col: int, row: int, *, center_x: float, center_y: float,
    cell_px: float, mid_col: float, mid_row: float,
) -> tuple[float, float]:
    """Project the map's eighth-cell coordinates into a calibrated client rect."""
    return center_x + (col - mid_col) * cell_px / 8, center_y + (row - mid_row) * cell_px / 8


def target_level_label(level_key: int, source_path: str | None) -> str:
    if str(level_key).startswith("900") or (source_path and "/daily/" in source_path):
        return "每日关卡"
    return "第二关" if source_path is None or "/world/" in source_path else "目标关卡"


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def game_client_rect(title: str = GAME_TITLE) -> tuple[int, int, int, int, int] | None:
    """Return (hwnd, screen-x, screen-y, width, height) for one visible game window."""
    user32 = ctypes.windll.user32
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _unused):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value == title:
            matches.append(hwnd)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    if len(matches) != 1:
        return None
    hwnd = matches[0]
    client = RECT()
    origin = POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(client)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return hwnd, origin.x, origin.y, client.right - client.left, client.bottom - client.top


def _port_running(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _capture_confdir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "sheep-helper" / "mitmproxy"


def _owned_capture_pid(capture_dir: Path, port: int) -> int | None:
    """Identify only the helper's own listening process, not a launcher wrapper."""
    preferred_pid = None
    try:
        status = json.loads((capture_dir / "game-capture-status.json").read_text(encoding="utf-8"))
        candidate = status.get("listener_pid") if isinstance(status, dict) else None
        if type(candidate) is int and candidate > 0:
            preferred_pid = candidate
    except (OSError, ValueError):
        pass

    def query(pid: int | None) -> list[dict[str, object]]:
        if pid is not None:
            # Query the PID recorded by our addon first. Enumerating every TCP
            # owner and then asking CIM about each one can exceed the old six
            # second timeout on otherwise healthy Windows installations.
            command = (
                f"$listener = Get-NetTCPConnection -LocalPort {port} -State Listen "
                f"-ErrorAction SilentlyContinue | Where-Object {{ $_.OwningProcess -eq {pid} }} "
                "| Select-Object -First 1; "
                f"if ($null -ne $listener) {{ Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" "
                "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress }"
            )
        else:
            command = (
                f"$owners = @(Get-NetTCPConnection -LocalPort {port} -State Listen "
                "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); "
                "$rows = foreach ($ownerPid in $owners) { Get-CimInstance Win32_Process "
                "-Filter \"ProcessId = $ownerPid\" | Select-Object ProcessId,CommandLine }; "
                "$rows | ConvertTo-Json -Compress"
            )
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                                    capture_output=True, text=True, timeout=12, check=True)
            rows = json.loads(result.stdout) if result.stdout.strip() else []
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        if isinstance(rows, dict):
            return [rows]
        return rows if isinstance(rows, list) else []

    directory = str(capture_dir.resolve()).casefold()
    for requested_pid in ((preferred_pid, None) if preferred_pid is not None else (None,)):
        matching = []
        for row in query(requested_pid):
            if not isinstance(row, dict):
                continue
            args = str(row.get("CommandLine") or "").casefold()
            if (f"--listen-port {port}" in args
                    and "game_capture_addon.py" in args
                    and "seed_recovery_addon.py" in args
                    and f"game_capture_dir={directory}" in args):
                matching.append(row.get("ProcessId"))
        if len(matching) == 1 and type(matching[0]) is int:
            return matching[0]
    return None


def _find_mitmdump() -> str | None:
    found = shutil.which("mitmdump")
    if found:
        return found
    project_root = (Path(sys.executable).resolve().parent.parent
                    if getattr(sys, "frozen", False)
                    else Path(__file__).resolve().parents[2])
    return shutil.which("mitmdump", path=str(project_root / ".venv" / "Scripts"))


def _start_capture(capture_dir: Path, *, port: int = CAPTURE_PORT) -> subprocess.Popen[bytes] | None:
    if _port_running(port):
        owned_pid = _owned_capture_pid(capture_dir, port)
        if owned_pid is None:
            raise RuntimeError(f"抓包端口 {port} 已被其他进程占用；请先关闭旧监听器")
        # A listener left by a previous GUI process may still accept TCP while
        # its one-file extraction directory and addon runtime are already
        # gone. Never reuse it across app launches: restart only the listener
        # whose command line and capture directory we verified above. Within
        # one GUI process ``self.capture_process`` is retained, so consecutive
        # capture cycles still reuse the healthy current listener.
        if owned_pid == os.getpid():
            raise RuntimeError("抓包进程识别异常；未关闭当前程序")
        os.kill(owned_pid, signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        while _port_running(port):
            if time.monotonic() >= deadline:
                raise RuntimeError("旧抓包监听器仍占用端口；请关闭旧版后重试")
            time.sleep(0.1)
    mitmdump = _find_mitmdump()
    if mitmdump is None:
        raise RuntimeError("找不到 mitmdump；请先安装 mitmproxy，并确保其 Scripts 目录在 PATH 中")
    confdir = _capture_confdir()
    if not (confdir / "mitmproxy-ca.pem").is_file():
        raise RuntimeError(f"找不到已配置的 mitmproxy 证书目录：{confdir}")
    enabled, server = _read_proxy()
    upstream_port = int(server.rsplit(":", 1)[1]) if enabled and server in SUPPORTED_UPSTREAMS else UPSTREAM_PORT
    upstream_deadline = time.monotonic() + 15.0
    while not _port_running(upstream_port):
        if time.monotonic() >= upstream_deadline:
            raise RuntimeError(f"Clash 上游代理 {UPSTREAM_HOST}:{upstream_port} 未就绪；启动 Clash 后重新打开助手")
        time.sleep(0.25)
    addon_dir = Path(__file__).resolve().parent
    bundle_dir = Path(sys._MEIPASS).resolve() if getattr(sys, "frozen", False) else None
    if bundle_dir is not None:
        addon_dir = prepare_capture_runtime(addon_dir, capture_dir / ".capture-runtime")
    capture_addon = addon_dir / "game_capture_addon.py"
    seed_addon = addon_dir / "seed_recovery_addon.py"
    if not capture_addon.is_file() or not seed_addon.is_file():
        raise RuntimeError("抓包插件源码缺失；请重新构建 EXE")

    capture_dir.mkdir(parents=True, exist_ok=True)
    command = [
        mitmdump, "--set", f"confdir={confdir}",
        "--mode", f"upstream:http://{UPSTREAM_HOST}:{upstream_port}",
        "--listen-host", "127.0.0.1", "--listen-port", str(port),
        "-s", capture_addon,
        "--set", f"game_capture_dir={capture_dir.resolve()}", "-s", seed_addon,
        "--set", f"seed_recovery_dir={capture_dir.resolve()}", "--quiet",
    ]
    environment = os.environ.copy()
    addon_parent = str(capture_addon.parent.parent)
    python_paths = environment.get("PYTHONPATH", "").split(os.pathsep)
    if bundle_dir is not None:
        python_paths = [entry for entry in python_paths
                        if entry and not Path(entry).resolve().is_relative_to(bundle_dir)]
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (addon_parent, *python_paths)))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if bundle_dir is not None:
        environment["PATH"] = os.pathsep.join(
            entry for entry in environment.get("PATH", "").split(os.pathsep)
            if entry and not Path(entry).resolve().is_relative_to(bundle_dir)
        )
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        # The proxy outlives this GUI; it must not keep a pipe to a closed parent.
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=flags, env=environment)
    finally:
        if bundle_dir is not None:
            ctypes.windll.kernel32.SetDllDirectoryW(str(bundle_dir))
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if _port_running(port):
            return process
        if process.poll() is not None:
            raise RuntimeError(f"mitmdump 启动失败，退出码 {process.returncode}")
        time.sleep(0.1)
    process.terminate()
    raise RuntimeError(f"mitmdump 未在 20 秒内开始监听本机 {port} 端口")


class GuideApp:
    def __init__(
        self, *, capture_dir: Path, cards: Path | None = None, coverage: Path | None = None,
        solution: Path | None = None, latest: bool = False, start_capture: bool = True,
        resume: bool = False, timeout: float | None = None,
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("桌面高亮界面目前只支持 Windows")
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
        self.capture_dir = capture_dir
        self.capture_proxy = CaptureProxy(capture_dir)
        try:
            recover_stale_proxy(self.capture_proxy.path)
            proxy_recovery_error = None
        except (OSError, ValueError) as error:
            proxy_recovery_error = str(error)
        self.settings_file = capture_dir / "guide-settings.json"
        self.timeout = load_timeout(self.settings_file) if timeout is None else validate_timeout(timeout)
        self.latest = latest
        self.start_capture_enabled = start_capture
        self.resume_requested = resume
        self.events: queue.Queue[tuple[str, object] | tuple[str, object, int]] = queue.Queue()
        self.closed = threading.Event()
        self._capture_lock = threading.Lock()
        self.capture_process: subprocess.Popen[bytes] | None = None
        self.capture_epoch = 0
        self.guide: GuidedPlay | None = None
        self.positions: dict[str, tuple[int, int]] = {}
        self._stable_alignment_ids: set[str] = set()
        self.mid_col = 0.0
        self.mid_row = 0.0
        self.calibrated = False
        self.paused = False
        self.pending_revive = False
        self.waiting_for_second = False
        self.target_label = "第二关"
        self.face_verified = True
        self.auto_verified = False
        self.calibration_pending = False
        self.calibration_attempted = False
        self.auto_click_enabled = False
        self.auto_click_pending_step = -1
        self.last_auto_click = 0.0
        self._mouse_was_down = False
        self._pending_click_step = -1
        self._snapshot_step = -1
        self._target_snapshot = None
        self._target_bbox: tuple[int, int, int, int] | None = None
        self._target_adjustment = (0.0, 0.0)
        self._target_adjustment_step = -1
        self.last_rect: tuple[int, int, int, int, int] | None = None
        self.calibration_file = capture_dir / "guide-calibration.json"
        self.saved_calibration = self._read_calibration()

        self.root = tk.Tk()
        self.root.title("羊了个羊：星球 · 通关指引")
        self.root.geometry("560x750")
        self.root.minsize(520, 700)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value=(f"上次临时代理未能恢复：{proxy_recovery_error}"
                                          if proxy_recovery_error else
                                          "点击「开始抓包」，再开启新局；第一关请自行通关"))
        self.step_text = tk.StringVar(value="尚无完整路线 · 先开始抓包")
        self.route_badge = tk.StringVar(value="等待新局")
        self.progress_text = tk.StringVar(value="路线待生成")
        self.center_x = tk.DoubleVar(value=180)
        self.center_y = tk.DoubleVar(value=250)
        self.cell_px = tk.DoubleVar(value=42)
        self.background_click = tk.BooleanVar(value=False)
        self.timeout_text = tk.StringVar(value=f"{self.timeout:g}")
        self.background_frames: BackgroundFrames | None = None
        self._build_controls()

        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.wm_attributes("-transparentcolor", TRANSPARENT)
        self.overlay.configure(bg=TRANSPARENT)
        self.canvas = tk.Canvas(self.overlay, bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.overlay.withdraw()
        self.root.update_idletasks()

        if cards is not None or coverage is not None or solution is not None:
            if cards is None or coverage is None or solution is None:
                raise ValueError("离线打开须同时提供 --cards、--coverage、--solution")
            self._load_explicit(cards, coverage, solution)
            self.capture_button.configure(state="disabled", text="离线路线")
        else:
            self.started_at = 0.0
        self.root.after(150, self._tick)
        self.root.after(20, self._poll_click)

    def _build_controls(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 12))
        tk.Label(header, text="✦", font=("Segoe UI Symbol", 26), fg=ACCENT, bg=BG).pack(side="left", padx=(0, 12))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="星球通关指引", font=("Microsoft YaHei UI", 17, "bold"),
                 fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(titles, text="逐步标记 · 可选自动点击", font=("Microsoft YaHei UI", 9),
                 fg=MUTED, bg=BG).pack(anchor="w")
        tk.Label(header, textvariable=self.route_badge, font=("Microsoft YaHei UI", 9, "bold"),
                 fg=ACCENT, bg=PANEL, padx=10, pady=6).pack(side="right")

        status_card = tk.Frame(self.root, bg=PANEL, padx=16, pady=12)
        status_card.pack(fill="x", padx=20, pady=(0, 12))
        tk.Label(status_card, text="当前状态", font=("Microsoft YaHei UI", 9, "bold"),
                 fg=ACCENT, bg=PANEL).pack(anchor="w")
        tk.Label(status_card, textvariable=self.status, wraplength=405, justify="left",
                 font=("Microsoft YaHei UI", 10), fg=TEXT, bg=PANEL).pack(anchor="w", pady=(5, 0))

        step_card = tk.Frame(self.root, bg=PANEL, padx=16, pady=12)
        step_card.pack(fill="x", padx=20, pady=(0, 14))
        step_header = tk.Frame(step_card, bg=PANEL)
        step_header.pack(fill="x")
        tk.Label(step_header, text="下一步", font=("Microsoft YaHei UI", 9, "bold"),
                 fg=ACCENT, bg=PANEL).pack(side="left")
        tk.Label(step_header, textvariable=self.progress_text, font=("Microsoft YaHei UI", 9),
                 fg=MUTED, bg=PANEL).pack(side="right")
        tk.Label(step_card, textvariable=self.step_text, wraplength=405, justify="left",
                 font=("Microsoft YaHei UI", 12, "bold"), fg=TEXT, bg=PANEL).pack(anchor="w", pady=(5, 0))
        self.progress_bar = tk.Canvas(step_card, height=4, bg="#263a52", highlightthickness=0)
        self.progress_bar.pack(fill="x", pady=(12, 0))
        self.progress_fill = self.progress_bar.create_rectangle(0, 0, 0, 4, fill=ACCENT, outline="")
        self.progress_bar.bind("<Configure>", lambda _event: self._paint_progress())

        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=20)
        self.capture_button = tk.Button(actions, text="开始抓包", command=self._begin_capture,
                                        bg=ACCENT, fg=BG, activebackground="#82f2d1",
                                        relief="flat", bd=0, padx=16, pady=10,
                                        font=("Microsoft YaHei UI", 10, "bold"))
        self.capture_button.pack(side="left")
        self.latest_button = tk.Button(actions, text="载入最近抓包", command=self._load_latest_capture,
                                       bg=BG, fg=ACCENT, activebackground=BG,
                                       relief="flat", bd=0, padx=8, pady=10,
                                       font=("Microsoft YaHei UI", 9))
        self.latest_button.pack(side="left", padx=(6, 0))
        self.confirm_button = tk.Button(actions, text="我已点此牌", command=self._confirm, state="disabled",
                                        bg="#314867", fg=TEXT, activebackground="#426083",
                                        disabledforeground=MUTED, relief="flat", bd=0, padx=14, pady=10,
                                        font=("Microsoft YaHei UI", 10))
        self.confirm_button.pack(side="left", padx=8)
        tk.Button(actions, text="暂停", command=self._pause, bg="#382d3b", fg="#ffb2b2",
                  activebackground="#503544", relief="flat", bd=0, padx=12, pady=10,
                  font=("Microsoft YaHei UI", 10)).pack(side="right")

        self.special_button = tk.Button(self.root, text="道具或复活完成后在这里确认",
                                        command=self._confirm_special, state="disabled",
                                        bg="#314867", fg=TEXT, activebackground="#426083",
                                        disabledforeground=MUTED, relief="flat", bd=0,
                                        padx=14, pady=8, font=("Microsoft YaHei UI", 10, "bold"))
        self.special_button.pack(fill="x", padx=20, pady=(10, 0))

        tools_row = tk.Frame(self.root, bg=BG)
        tools_row.pack(fill="x", padx=20, pady=(15, 0))
        self.auto_button = tk.Button(tools_row, text="⟳ 重新自动对齐", command=self._request_auto_calibration,
                                     state="disabled", bg=BG, fg=ACCENT, relief="flat", bd=0,
                                     activebackground=BG, activeforeground="#8df5d7")
        self.auto_button.pack(side="left")
        self.auto_click_button = tk.Button(tools_row, text="自动点击：关", command=self._toggle_auto_click,
                                           state="disabled", bg=BG, fg=MUTED, relief="flat", bd=0,
                                           activebackground=BG, activeforeground=ACCENT)
        self.auto_click_button.pack(side="left", padx=(14, 0))
        self.advanced_button = tk.Button(tools_row, text="校准微调 ▾", command=self._toggle_advanced,
                                         bg=BG, fg=MUTED, relief="flat", bd=0,
                                         activebackground=BG, activeforeground=TEXT)
        self.advanced_button.pack(side="right")
        timeout_row = tk.Frame(self.root, bg=BG)
        timeout_row.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(timeout_row, text="求解时限", bg=BG, fg=TEXT).pack(side="left")
        self.timeout_input = ttk.Combobox(timeout_row, textvariable=self.timeout_text,
                                         values=(20, 60, 120, 300, 600), width=7)
        self.timeout_input.pack(side="left", padx=(8, 4))
        tk.Label(timeout_row, text="秒", bg=BG, fg=TEXT).pack(side="left")
        self.timeout_apply_button = tk.Button(timeout_row, text="应用", command=self._apply_timeout,
                                             bg=PANEL, fg=ACCENT, relief="flat", padx=12)
        self.timeout_apply_button.pack(side="left", padx=8)
        tk.Label(timeout_row, text="下次求解生效 · 自动记住", bg=BG, fg=MUTED).pack(side="left")
        tk.Checkbutton(self.root, text="后台模拟点击（不占鼠标，窗口请勿最小化）",
                       variable=self.background_click, command=self._toggle_background_click,
                       bg=BG, fg=TEXT, selectcolor=PANEL, activebackground=BG,
                       activeforeground=TEXT).pack(anchor="w", padx=20, pady=(8, 0))

        self.advanced = tk.Frame(self.root, bg=PANEL, padx=12, pady=10)
        row = tk.Frame(self.advanced, bg=PANEL)
        row.pack(fill="x")
        for label, variable in (("中心 X", self.center_x), ("中心 Y", self.center_y), ("牌间距", self.cell_px)):
            tk.Label(row, text=label, fg=MUTED, bg=PANEL).pack(side="left")
            tk.Spinbox(row, from_=-1000, to=2000, increment=2, width=5, textvariable=variable,
                       bg=BG, fg=TEXT, buttonbackground=PANEL, insertbackground=TEXT,
                       relief="flat").pack(side="left", padx=(3, 8))
        self.calibrate_button = tk.Button(self.advanced, text="应用微调位置", command=self._toggle_calibration,
                                          bg="#314867", fg=TEXT, relief="flat", bd=0, padx=12, pady=6)
        self.calibrate_button.pack(anchor="w", pady=(8, 0))
        tk.Label(self.root, text="自动点击默认关闭；支持取回移出牌，道具与复活需手动完成并确认。",
                 wraplength=430, justify="left", font=("Microsoft YaHei UI", 9),
                 fg=MUTED, bg=BG).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(self.root, text="若点错、换局或标记不准，请立即暂停。",
                 font=("Microsoft YaHei UI", 9), fg="#ffaaa4", bg=BG).pack(anchor="w", padx=20)

    def _paint_progress(self) -> None:
        fraction = (self.guide.confirmed / len(self.guide.plan)
                    if self.guide is not None and self.guide.plan else 0.0)
        self.progress_bar.coords(self.progress_fill, 0, 0, round(self.progress_bar.winfo_width() * fraction), 4)

    def _update_progress(self) -> None:
        self.progress_text.set(f"{self.guide.confirmed} / {len(self.guide.plan)} 步"
                               if self.guide is not None else "路线待生成")
        self._paint_progress()

    def _toggle_advanced(self) -> None:
        if self.advanced.winfo_manager():
            self.advanced.pack_forget()
            self.advanced_button.configure(text="校准微调 ▾")
            self.root.geometry("560x750")
        else:
            self.advanced.pack(fill="x", padx=20, pady=(10, 0))
            self.advanced_button.configure(text="收起微调 ▴")
            self.root.geometry("560x850")

    def _load_latest_capture(self) -> None:
        capture = find_captured_game(self.capture_dir, not_before=time.time() - 3600)
        if capture is None:
            self.status.set("最近一小时没有完整的地图和种子；请点击「开始抓包」并进入新局")
            return
        self.latest = True
        self.resume_requested = True
        self._begin_capture()

    def _apply_timeout(self, *, show_status: bool = True) -> bool:
        try:
            seconds = save_timeout(self.settings_file, self.timeout_text.get())
        except (OSError, ValueError) as error:
            self.status.set(f"求解时限未保存：{error}")
            return False
        self.timeout = seconds
        self.timeout_text.set(f"{seconds:g}")
        if show_status:
            self.status.set(f"求解时限已保存为 {seconds:g} 秒，下次求解生效。超时后可调高时限，保持牌局不变并载入最近抓包重试")
        return True

    def _begin_capture(self) -> None:
        if not self._apply_timeout(show_status=False):
            self.latest = False
            return
        if not self.latest:
            self.resume_requested = False
        try:
            (self.capture_dir / CAPTURE_DONE_MARKER).unlink(missing_ok=True)
        except OSError as error:
            self.latest = False
            self.status.set(f"无法重新开始抓包：{error}")
            return
        self.capture_epoch += 1
        epoch = self.capture_epoch
        self._disable_auto_click()
        self.guide = None
        self._update_progress()
        self.positions = {}
        self.calibrated = False
        self.face_verified = True
        self.route_badge.set("正在抓包")
        self.auto_verified = False
        self.waiting_for_second = False
        self.calibration_attempted = False
        self.paused = False
        self.overlay.withdraw()
        self.confirm_button.configure(state="disabled", text="等待完整路线")
        self.special_button.configure(state="disabled", text="道具或复活完成后在这里确认")
        self.auto_button.configure(state="disabled", text="⟳ 重新自动对齐")
        self.calibrate_button.configure(state="disabled")
        self.step_text.set("等待目标关卡完整路线")
        self.started_at = time.time()
        self.capture_button.configure(state="disabled", text="正在准备…")
        self.latest_button.configure(state="disabled")
        self.status.set("正在准备抓包；两种模式的第一关都由你手动通过，程序只指引第二关")
        if self.start_capture_enabled:
            threading.Thread(target=self._capture_worker, args=(not self.latest, epoch), daemon=True).start()
        else:
            self.events.put(("capture_ready", None, epoch))

    def _capture_worker(self, route_traffic: bool, epoch: int | None = None) -> None:
        epoch = self.capture_epoch if epoch is None else epoch
        try:
            process = (self.capture_process if self.capture_process is not None
                       and self.capture_process.poll() is None else _start_capture(self.capture_dir))
            if self.closed.is_set() or epoch != self.capture_epoch:
                return
            with self._capture_lock:
                if self.closed.is_set() or epoch != self.capture_epoch:
                    return
                if route_traffic:
                    self.capture_proxy.activate()
                if self.closed.is_set() or epoch != self.capture_epoch:
                    self.capture_proxy.restore()
                    return
                self.events.put(("capture_ready", (process, route_traffic), epoch))
        except (OSError, ValueError, RuntimeError) as error:
            try:
                with self._capture_lock:
                    if epoch == self.capture_epoch:
                        self.capture_proxy.restore()
            except (OSError, ValueError) as restore_error:
                error = RuntimeError(f"{error}；恢复原代理失败：{restore_error}")
            self.events.put(("capture_error", str(error), epoch))

    def _request_auto_calibration(self) -> None:
        if (self.guide is None or self.last_rect is None or self.calibration_pending
                or self.paused or self.waiting_for_second):
            return
        self._disable_auto_click()
        self.calibration_pending = True
        self.calibration_attempted = True
        self.status.set("正在核对地图位置；牌面未核验时不会显示点击标记…"
                        if not self.face_verified else "正在根据当前牌面自动对齐标记…")
        self.root.withdraw()
        self.overlay.withdraw()
        self.root.after(120, self._capture_alignment_frame)

    def _capture_alignment_frame(self) -> None:
        if self.guide is None or self.closed.is_set():
            self.calibration_pending = False
            return
        epoch = self.capture_epoch
        try:
            _hwnd, x, y, width, height = self.last_rect
            frame = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
            points = self._available_points()
            target = None
            if self.guide is not None and self.guide.next_action is not None:
                kind, index = self.guide.next_action
                if kind == "board":
                    target = self.positions[self.guide.board.ids[index]]
            threading.Thread(target=self._alignment_worker,
                             args=(frame, points, self.mid_col, self.mid_row,
                                    self.target_label == "每日关卡", target, epoch), daemon=True).start()
        except (OSError, ValueError, RuntimeError, AttributeError) as error:
            self.events.put(("alignment_error", str(error), epoch))
        finally:
            if not self.closed.is_set():
                self.root.deiconify()
                self.root.lift()

    def _alignment_worker(
        self, frame, points: list[tuple[int, int]], mid_col: float, mid_row: float,
        daily: bool = False, target: tuple[int, int] | None = None,
        epoch: int | None = None,
    ) -> None:
        try:
            matcher = find_daily_alignment if daily else find_alignment
            result = matcher(frame, points, mid_col=mid_col, mid_row=mid_row)
            if result is not None and not daily and not self._valid_world_scale(result.cell_px / frame.width):
                # Repeated tile edges can produce a convincing fit at the
                # wrong pitch. That error grows with distance from the center.
                result = None
            if result is None and not daily:
                # Icon-heavy world boards can defeat the older corner-color
                # matcher. Reuse the exposed-card matcher as a conservative
                # second pass, without changing the world map/solver path.
                result = find_daily_alignment(frame, points, mid_col=mid_col, mid_row=mid_row)
            if result is not None and not daily and not self._valid_world_scale(result.cell_px / frame.width):
                result = None
            if result is not None and target is not None:
                x, y = tile_center(target[0], target[1], center_x=result.center_x,
                                   center_y=result.center_y, cell_px=result.cell_px,
                                   mid_col=mid_col, mid_row=mid_row)
                refined = find_exposed_card_center(frame, x, y, result.cell_px)
                if refined is not None:
                    result = Alignment(result.center_x + refined[0] - x,
                                       result.center_y + refined[1] - y,
                                       result.cell_px, result.confidence)
                elif not is_exposed_card(frame, x, y, result.cell_px):
                    result = None
            self.events.put(("alignment", result, self.capture_epoch if epoch is None else epoch))
        except (OSError, ValueError) as error:
            self.events.put(("alignment_error", str(error), self.capture_epoch if epoch is None else epoch))

    def _available_points(self) -> list[tuple[int, int]]:
        available_ids = [self.guide.board.ids[index]
                         for index in range(self.guide.board.size)
                         if self.guide.session.state.available & (1 << index)]
        stable = [self.positions[tile_id] for tile_id in available_ids
                  if tile_id in self._stable_alignment_ids]
        if len(stable) >= 4:
            cols, rows = zip(*stable)
            if max(cols) - min(cols) >= 8 and max(rows) - min(rows) >= 8:
                return stable
        return [self.positions[tile_id] for tile_id in available_ids]

    def _read_calibration(self) -> dict[str, float] | None:
        try:
            record = json.loads(self.calibration_file.read_text(encoding="utf-8"))
            if (isinstance(record, dict)
                    and record.get("stage") == ("daily" if self.target_label == "每日关卡" else "second")
                    and (self.target_label != "每日关卡" or record.get("version") == 2)
                    and self._valid_target_calibration(record)):
                return record
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def _valid_calibration(record: dict[str, object]) -> bool:
        bounds = {"center_x_ratio": (0.1, 0.9), "center_y_ratio": (0.1, 0.85),
                  "cell_ratio": (0.03, 0.2)}
        return all(type(record.get(key)) in (int, float)
                   and math.isfinite(record[key]) and low <= record[key] <= high
                   for key, (low, high) in bounds.items())

    @staticmethod
    def _valid_world_scale(cell_ratio: float) -> bool:
        # The dense world board has about eleven card pitches across the
        # mini-program client. The old width/9 preview was visibly too large.
        return 0.075 <= cell_ratio <= 0.105

    def _valid_target_calibration(self, record: dict[str, object]) -> bool:
        return (self._valid_calibration(record)
                and (self.target_label == "每日关卡"
                     or self._valid_world_scale(float(record["cell_ratio"]))))

    def _save_calibration(self) -> None:
        if self.last_rect is None:
            return
        _hwnd, _x, _y, width, height = self.last_rect
        if width <= 0 or height <= 0:
            raise ValueError("游戏窗口尺寸无效，请恢复窗口后重新对齐")
        payload = {
            "version": 2,
            "stage": "daily" if self.target_label == "每日关卡" else "second",
            "center_x_ratio": self.center_x.get() / width,
            "center_y_ratio": self.center_y.get() / height,
            "cell_ratio": self.cell_px.get() / width,
        }
        if not self._valid_target_calibration(payload):
            raise ValueError("校准位置或牌间距超出合理范围，请重新自动对齐")
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.saved_calibration = payload

    def _apply_saved_calibration(self, width: int, height: int) -> None:
        if self.saved_calibration is not None:
            self.center_x.set(round(width * self.saved_calibration["center_x_ratio"], 1))
            self.center_y.set(round(height * self.saved_calibration["center_y_ratio"], 1))
            self.cell_px.set(round(width * self.saved_calibration["cell_ratio"], 1))
        else:
            self.center_x.set(width / 2)
            self.center_y.set(height * (0.44 if self.target_label == "每日关卡" else 0.46))
            self.cell_px.set(width / 11)

    def _alignment_in_bounds(self, alignment: Alignment) -> bool:
        if self.last_rect is None:
            return False
        width, height = self.last_rect[3:]
        return width > 0 and height > 0 and self._valid_target_calibration({
            "center_x_ratio": alignment.center_x / width,
            "center_y_ratio": alignment.center_y / height,
            "cell_ratio": alignment.cell_px / width,
        })

    def _make_click_through(self) -> bool:
        """Apply mouse passthrough to Tk's outer HWND, not its child HWND."""
        user32 = ctypes.windll.user32
        get_ancestor = user32.GetAncestor
        get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
        get_ancestor.restype = wintypes.HWND
        hwnd = get_ancestor(self.overlay.winfo_id(), 2)  # GA_ROOT
        if not hwnd:
            return False
        get_style = user32.GetWindowLongPtrW
        get_style.argtypes = (wintypes.HWND, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        set_style = user32.SetWindowLongPtrW
        set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        set_style.restype = ctypes.c_ssize_t
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x20
        WS_EX_LAYERED = 0x80000
        WS_EX_TOOLWINDOW = 0x80
        WS_EX_NOACTIVATE = 0x08000000
        required = WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        existing = get_style(hwnd, GWL_EXSTYLE)
        if existing & required != required:
            set_style(hwnd, GWL_EXSTYLE, existing | required)
            set_window_pos = user32.SetWindowPos
            set_window_pos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT)
            set_window_pos.restype = wintypes.BOOL
            # Refresh extended styles without moving or activating the overlay.
            if not set_window_pos(hwnd, None, 0, 0, 0, 0, 0x37):
                return False
        return get_style(hwnd, GWL_EXSTYLE) & required == required

    def _load_explicit(self, cards: Path, coverage: Path, solution: Path) -> None:
        board = Board.from_files(cards, coverage)
        plan = load_verified_plan(board, solution)
        record = json.loads(cards.read_text(encoding="utf-8"))
        label = target_level_label(int(record["level_key"]), None)
        self._set_guide(board, plan, cards, solution.with_suffix(".progress.json"),
                        target_label=label)

    def _watch_capture(self, epoch: int) -> None:
        seen: set[Path] = set()
        last_status = ""
        next_map_fetch = 0.0
        map_fetch_error: str | None = None
        capture_deadline = time.monotonic() + 120.0
        start_record = self.capture_dir / "latest-game-start.json"
        last_start_mtime = start_record.stat().st_mtime_ns if start_record.is_file() else 0
        while not self.closed.is_set() and epoch == self.capture_epoch:
            if time.monotonic() >= capture_deadline:
                self.events.put(("capture_stopped", "两分钟内未收到完整关卡数据；临时代理将恢复，请重新抓包", epoch))
                return
            if self.capture_process is not None and self.capture_process.poll() is not None:
                self.events.put(("capture_stopped", "抓包进程意外退出；请检查 mitmdump 和微信代理配置", epoch))
                return
            current_start_mtime = start_record.stat().st_mtime_ns if start_record.is_file() else 0
            if current_start_mtime > last_start_mtime:
                last_start_mtime = current_start_mtime
                self.events.put(("new_game", "已收到关卡入口数据；正在等待地图和种子…", epoch))
            capture = find_captured_game(self.capture_dir, not_before=self.started_at)
            if capture is None:
                if time.monotonic() >= next_map_fetch:
                    try:
                        fetched = fetch_missing_map(self.capture_dir, not_before=self.started_at)
                    except (OSError, ValueError) as error:
                        next_map_fetch = time.monotonic() + 10.0
                        map_fetch_error = str(error)
                    else:
                        map_fetch_error = None
                        next_map_fetch = time.monotonic() + 2.0
                        if fetched is not None:
                            self.events.put(("status", "已补取并校验本局地图；正在解包求解…", epoch))
                            continue
                map_dir = self.capture_dir / "maps"
                try:
                    has_recent_map = any(path.stat().st_mtime >= self.started_at
                                         for path in map_dir.glob("*.map"))
                except OSError:
                    has_recent_map = False
                if has_recent_map:
                    status = "已收到地图，仍在等待本局种子；可重新进入关卡触发请求"
                elif self.capture_proxy.path.is_file():
                    status = "等待开局请求：请在游戏内开新局；若仍无请求，再重新打开小程序。第一关自行通关"
                else:
                    status = "等待目标关卡地图与种子；第一关可自行通关"
                if map_fetch_error is not None:
                    status += f"；地图主动补取失败：{map_fetch_error}"
                if status != last_status:
                    self.events.put(("status", status, epoch))
                    last_status = status
                self.closed.wait(1.0)
                continue
            if capture.seed_path in seen:
                self.closed.wait(1.0)
                continue
            seen.add(capture.seed_path)
            target_label = target_level_label(capture.level_key, capture.source_path)
            self.events.put(("captured", f"已抓到{target_label} {capture.tile_count} 张牌；正在求完整路线", epoch))
            try:
                prepared = prepare_captured_game(capture, self.capture_dir)
                board, plan = solve_prepared_game(
                    prepared, timeout=self.timeout,
                    cancelled=lambda: self.closed.is_set() or epoch != self.capture_epoch,
                    progress=lambda status: self.events.put(("status", status, epoch)),
                )
                self.events.put(("ready", (board, plan, prepared.cards_path,
                                           prepared.solution_path.with_suffix(".progress.json"), True,
                                           target_label), epoch))
                return
            except (OSError, ValueError, RuntimeError) as error:
                self.events.put(("unsolved", f"本局暂不能指引：{error}", epoch))
                return

    def _finish_capture_recording(self) -> str | None:
        # The proxy must stay alive: Clash still routes the mini-program through
        # port 18888. This marker stops our addons from saving later traffic.
        error_message = None
        try:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            (self.capture_dir / CAPTURE_DONE_MARKER).touch()
        except OSError as error:
            error_message = str(error)
        try:
            self.capture_proxy.restore()
        except (OSError, ValueError) as error:
            error_message = f"原系统代理恢复失败：{error}"
        self.capture_button.configure(text="抓包完成·求解中", state="disabled")
        return error_message

    def _reset_capture_cycle(self, reason: str) -> None:
        """Stop recording this game, but keep WeChat's proxy route alive."""
        # Invalidate queued worker results before changing the proxy or UI.
        self.capture_epoch += 1
        try:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            (self.capture_dir / CAPTURE_DONE_MARKER).touch()
            stop_note = "本局抓包已停止"
        except OSError as error:
            stop_note = f"本局抓包停止标记写入失败：{error}"
        try:
            with self._capture_lock:
                if self.capture_proxy.restore():
                    stop_note += "，原系统代理已恢复"
        except (OSError, ValueError) as error:
            stop_note += f"；原系统代理恢复失败：{error}"
        self.guide = None
        self._disable_auto_click()
        self.positions = {}
        self.mid_col = 0.0
        self.mid_row = 0.0
        self.calibrated = False
        self.face_verified = True
        self.auto_verified = False
        self.calibration_pending = False
        self.calibration_attempted = False
        self.waiting_for_second = False
        self.target_label = "第二关"
        self.paused = False
        self.pending_revive = False
        self.last_auto_click = 0.0
        self._target_snapshot = None
        self._target_bbox = None
        self._snapshot_step = -1
        self._pending_click_step = -1
        self._mouse_was_down = False
        self.last_rect = None
        self.calibration_file = self.capture_dir / "guide-calibration.json"
        self.saved_calibration = self._read_calibration()
        self.started_at = time.time()
        self.latest = False  # A retry must not reopen the failed seed.
        self.overlay.withdraw()
        self.capture_button.configure(text="开始抓包", state="normal")
        self.latest_button.configure(state="normal")
        self.confirm_button.configure(text="我已点此牌", state="disabled")
        self.special_button.configure(state="disabled", text="道具或复活完成后在这里确认")
        self.auto_button.configure(text="⟳ 重新自动对齐", state="disabled")
        self.calibrate_button.configure(state="disabled")
        self.auto_click_button.configure(state="disabled")
        self.step_text.set("等待新局 · 请再次点击「开始抓包」")
        self.route_badge.set("等待新局")
        self._update_progress()
        self.status.set(f"{reason}；{stop_note}。下次先点击「开始抓包」，再进入新局")

    def _set_guide(self, board: Board, plan, cards_path: Path, progress_path: Path,
                   wait_for_second: bool = False, target_label: str = "第二关") -> None:
        cards = json.loads(cards_path.read_text(encoding="utf-8"))
        tiles = cards["tiles"]
        self.positions = {str(tile["id"]): (int(tile["col"]), int(tile["row"])) for tile in tiles}
        # Random-display cards can temporarily hide their face. Their map
        # coordinates remain valid for clicking, but they are poor visual
        # anchors for fitting the board grid.
        self._stable_alignment_ids = {
            str(tile["id"]) for tile in tiles if int(tile.get("mold_type", 1)) != 2
        }
        self.mid_col = (min(col for col, _ in self.positions.values()) + max(col for col, _ in self.positions.values())) / 2
        self.mid_row = (min(row for _, row in self.positions.values()) + max(row for _, row in self.positions.values())) / 2
        self.guide = GuidedPlay(board, tuple(plan), progress_path)
        if self.resume_requested and progress_path.is_file():
            self.guide.resume()
        self.calibrated = False
        self.auto_verified = False
        self.calibration_attempted = False
        self._target_snapshot = None
        self._snapshot_step = -1
        self.paused = False
        self.pending_revive = False
        self.waiting_for_second = wait_for_second
        self.target_label = target_label
        # Backflow games need server runtime faces. Fresh games without
        # match_data follow the client's exact seed-based assignment.
        available_ids = {board.ids[index] for index in range(board.size)
                         if self.guide.session.state.available & (1 << index)}
        observed_ids = set(cards.get("verified_visible_ids", []))
        self.face_verified = (
            target_label != "每日关卡"
            or cards.get("verification_mode") in {"server-runtime", "client-seed-fresh"}
            or (cards.get("verification_mode") == "exact-type"
                and len(available_ids & observed_ids) >= 4)
        )
        mode = cards.get("verification_mode")
        if target_label == "每日关卡":
            badge = {"server-runtime": "每日 · 服务器牌面",
                     "client-seed-fresh": "每日 · 新局种子"}.get(mode)
            badge = badge or ("每日 · 实测牌面" if self.face_verified else "每日 · 待核验")
        else:
            badge = f"{target_label} · 路线就绪"
        self.route_badge.set(badge)
        self.calibration_file = (self.capture_dir / "guide-calibration-daily.json"
                                 if target_label == "每日关卡" else self.capture_dir / "guide-calibration.json")
        self.saved_calibration = self._read_calibration()
        if self.last_rect is not None:
            self._apply_saved_calibration(self.last_rect[3], self.last_rect[4])
        self._disable_auto_click()
        self.calibrate_button.configure(state="normal")
        self.auto_button.configure(state="disabled" if wait_for_second else "normal",
                                   text=f"⟳ 立即检查{target_label}" if wait_for_second else "⟳ 重新自动对齐")
        if not self.face_verified:
            self.status.set("已抓到每日第二关地图，但牌面来源尚未核准；未核准前不会标记或点击")
        else:
            final = replay(board, plan)
            tools = ("移出和复活" if final.move_out_used and final.revive_used else
                     "仅移出" if final.move_out_used else "仅复活" if final.revive_used else "无道具")
            self.status.set(f"{target_label} {board.size} 张牌 · {tools}路线已准备；进入后按「我已进入{target_label}」。"
                            if wait_for_second else f"完整路线已验证：{board.size} 张 · {tools}。正在准备自动对齐…")
        self._update_step()

    def _update_step(self) -> None:
        self._update_progress()
        if self.guide is None:
            return
        if self.waiting_for_second:
            stage = "每日第二关" if self.target_label == "每日关卡" else self.target_label
            self.step_text.set(f"等待你确认进入{stage}；第一关请自行通关")
            self.confirm_button.configure(state="normal", text=f"我已进入{stage}")
            self.special_button.configure(state="disabled", text="道具或复活完成后在这里确认")
            return
        if not self.face_verified:
            self._disable_auto_click()
            self.step_text.set("每日关卡牌面尚未实测核对；这条路线不可使用，暂不指引点击")
            self.confirm_button.configure(state="disabled", text="牌面待核对")
            self.special_button.configure(state="disabled", text="道具或复活完成后在这里确认")
            self.auto_click_button.configure(state="disabled")
            self.overlay.withdraw()
            return
        if self.pending_revive:
            self.step_text.set("复活步骤：看完广告并在游戏中使用复活移出三张，完成后确认")
            self.confirm_button.configure(state="disabled", text="等待复活")
            self.special_button.configure(text="确认已完成复活",
                                          state="normal" if self.calibrated and not self.paused else "disabled")
            self.auto_click_button.configure(state="normal" if self.calibrated and self.auto_verified
                                             and not self.paused else "disabled")
            return
        if not self.calibrated:
            self.step_text.set(f"{self.target_label}路线已算出；准心预览待校准，请微调后再操作")
            self.confirm_button.configure(state="disabled", text="等待画面对齐")
            self.special_button.configure(state="disabled", text="道具或复活完成后在这里确认")
            return
        if self.guide.finished:
            self._disable_auto_click()
            self.auto_click_button.configure(state="disabled")
            self.step_text.set("路线完成；请核对游戏通关画面")
            self.confirm_button.configure(state="disabled")
            self.special_button.configure(state="disabled")
            return
        action = self.guide.next_action
        if action is None:
            self._disable_auto_click()
            self.auto_click_button.configure(state="disabled")
            self.step_text.set("路线结束，但游戏状态未清空；请暂停")
            self.confirm_button.configure(state="disabled")
            self.special_button.configure(state="disabled")
            return
        kind, value = action
        if kind == "board":
            col, row = self.positions[self.guide.board.ids[value]]
            cols, rows = zip(*self.positions.values())
            region = board_region(col, row, min_col=min(cols), max_col=max(cols),
                                  min_row=min(rows), max_row=max(rows))
            self.step_text.set(f"第 {self.guide.confirmed + 1} 步 · 点击{region}准心指向的{face_name(self.guide.board.types[value])}")
            self.confirm_button.configure(text="我已点此牌", state="normal" if self.calibrated and not self.paused else "disabled")
            self.special_button.configure(state="disabled", text="道具或复活完成后在这里确认")
        elif kind == "park":
            card = self.guide.session.state.parked[value][-1]
            self.step_text.set(f"第 {self.guide.confirmed + 1} 步：取回移出区第 {value + 1} 列的{face_name(self.guide.board.types[card])}")
            self.confirm_button.configure(text="我已取回此牌", state="normal" if self.calibrated and not self.paused else "disabled")
            self.special_button.configure(text="确认已取回此牌", state="normal" if self.calibrated and not self.paused else "disabled")
        else:
            self.step_text.set(f"第 {self.guide.confirmed + 1} 步：使用一次“移出三张”道具，完成后确认")
            self.confirm_button.configure(text="等待道具", state="disabled")
            self.special_button.configure(text="确认已使用移出道具", state="normal" if self.calibrated and not self.paused else "disabled")
        self.auto_click_button.configure(state="normal" if self.calibrated and self.auto_verified
                                         and not self.paused else "disabled")
        if kind not in ("board", "park") and self.auto_click_enabled:
            self.auto_click_button.configure(text="自动点击：待确认")
            self.status.set("自动点击已等待：请先在游戏中完成本步，再按面板确认")
        elif self.auto_click_enabled:
            self.auto_click_button.configure(text="自动点击：开")
        self._target_snapshot = None
        self._snapshot_step = -1
        self._pending_click_step = -1
        self._park_geometry_step = -1
        self._target_adjustment = (0.0, 0.0)
        self._target_adjustment_step = -1
        if kind in ("board", "park") and self.auto_verified and self.calibrated:
            self.root.after(250, self._capture_target_snapshot)

    def _toggle_calibration(self) -> None:
        if self.paused:
            return
        if not self.face_verified:
            self.status.set("每日关卡牌面未核对；微调只能改坐标，不能修复错误牌型，已拒绝放行")
            return
        if self.guide is None or self.last_rect is None or self.waiting_for_second:
            messagebox.showwarning("尚未就绪", "请等待完整路线，并恢复游戏窗口。")
            return
        try:
            self._save_calibration()
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        self.calibrated = True
        self.auto_verified = True
        self._disable_auto_click()
        self.status.set("已应用手动微调；请再次核对准心。自动点击可自行开启，目标牌会在点击前检查")
        self._update_step()

    def _confirm(self, *, visual_confirmed: bool = False) -> None:
        if self.waiting_for_second:
            if self.last_rect is None:
                self.status.set(f"尚未找到游戏窗口；请让{self.target_label}画面保持可见再试")
            else:
                self.waiting_for_second = False
                self.auto_button.configure(state="normal", text="⟳ 重新自动对齐")
                self._update_step()
                self._request_auto_calibration()
            return
        if not self.face_verified:
            return
        if self.guide is None or not self.calibrated:
            return
        if self.pending_revive or self.guide.next_action is None or self.guide.next_action[0] not in ("board", "park"):
            self.status.set("这一步需先完成道具或复活，再按下方专用确认键")
            return
        if not visual_confirmed:
            self._disable_auto_click()
        previous_revive = self.guide.session.state.revive_used
        try:
            self.guide.confirm_next()
        except (OSError, ValueError) as error:
            self._pause()
            messagebox.showerror("已暂停", str(error))
            return
        if self.guide.session.state.revive_used and not previous_revive:
            self.pending_revive = True
            self._update_step()
            if self.auto_click_enabled:
                self.auto_click_button.configure(text="自动点击：待复活")
            self.status.set("自动点击已等待：看完广告并使用复活道具后，请按下方「确认已完成复活」")
        else:
            self._update_step()
            if not visual_confirmed:
                self.status.set("已手动确认这一张；自动点击保持关闭，请核对下一张准心")

    def _confirm_special(self) -> None:
        if (self.guide is None or not self.calibrated or self.paused
                or self.waiting_for_second):
            return
        if self.pending_revive:
            self.pending_revive = False
            self._disable_auto_click()
            self._update_step()
            self.status.set("已确认复活完成；请核对下一张准心，再按需开启自动点击")
            return
        action = self.guide.next_action
        if action is None or action[0] not in ("move_out", "park"):
            return
        try:
            self.guide.confirm_next()
        except (OSError, ValueError) as error:
            self._pause()
            messagebox.showerror("已暂停", str(error))
            return
        self._disable_auto_click()
        self._update_step()
        self.status.set("已确认道具或移出区操作完成；请核对下一张准心，再按需开启自动点击")

    def _pause(self) -> None:
        self._reset_capture_cycle("已暂停并重置本局")

    def _disable_auto_click(self) -> None:
        self.auto_click_enabled = False
        self.auto_click_pending_step = -1
        self.auto_click_button.configure(text="自动点击：关", fg=MUTED)

    def _toggle_auto_click(self) -> None:
        if self.auto_click_enabled:
            self._disable_auto_click()
            self.status.set("自动点击已关闭；标记和手动下一步仍可使用")
            return
        if (self.guide is None or not self.calibrated or not self.auto_verified
                or self.waiting_for_second or self.paused):
            self.status.set(f"先确认{self.target_label}并核对准心位置，才能开启自动点击")
            return
        self.auto_click_enabled = True
        self.last_auto_click = time.monotonic()
        self.auto_click_button.configure(text="自动点击：开", fg=ACCENT)
        self.status.set("自动点击已开启；支持普通牌和移出牌，道具及复活请手动完成并确认")

    def _toggle_background_click(self) -> None:
        self._disable_auto_click()
        self._background_wait = None
        if self.background_frames is not None:
            self.background_frames.close()
            self.background_frames = None
        self._update_step()
        self.status.set("后台模拟点击已开启；核对准心后开启自动点击，可切换其他窗口，请勿最小化游戏"
                        if self.background_click.get() else "已恢复前台鼠标点击；请重新开启自动点击")

    def _grab_game_region(self, bbox):
        if not self.background_click.get():
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        if self.last_rect is None:
            raise OSError("游戏窗口不可用")
        hwnd, left, top, width, height = self.last_rect
        if self.background_frames is None:
            self.background_frames = BackgroundFrames()
        target = (hwnd, width, height)
        self.background_frames.request(target)
        frame = self.background_frames.frame(target)
        x1, y1, x2, y2 = bbox
        if not (left <= x1 < x2 <= left + width and top <= y1 < y2 <= top + height):
            raise OSError("截图范围超出游戏窗口")
        return frame.crop((x1 - left, y1 - top, x2 - left, y2 - top))

    def _maybe_auto_click(self, user32, hwnd: int, left: int, top: int,
                          width: int, height: int) -> None:
        if (not self.auto_click_enabled or self.guide is None or not self.auto_verified
                or not self.calibrated or self.paused or self.pending_revive
                or self.calibration_pending or self.waiting_for_second
                or self.guide.next_action is None or self.guide.next_action[0] not in ("board", "park")
                or time.monotonic() - self.last_auto_click < 1.0):
            return
        step = self.guide.confirmed
        if (self.auto_click_pending_step == step or self._pending_click_step == step
                or self._snapshot_step != step or self._target_snapshot is None
                or self._target_bbox is None
                or (not self.background_click.get() and
                    (user32.GetForegroundWindow() != hwnd or user32.GetAsyncKeyState(0x01) & 0x8000))):
            return
        geometry = self._target_geometry()
        if geometry is None:
            return
        x, y, cell = geometry
        if not (cell * 0.5 < x < width - cell * 0.5
                and cell * 0.5 < y < height - cell * 0.5):
            self._disable_auto_click()
            self.status.set("自动点击已停止：准心超出游戏区域，请重新对齐")
            return
        before, bbox = self._target_snapshot, self._target_bbox
        self.auto_click_pending_step = step
        self._pending_click_step = step
        self.last_auto_click = time.monotonic()
        self.confirm_button.configure(state="disabled")
        try:
            if self.background_click.get():
                post_click(hwnd, round(x), round(y))
            else:
                if not user32.SetCursorPos(round(left + x), round(top + y)):
                    raise OSError("无法定位鼠标")
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
        except (OSError, AttributeError) as error:
            self._disable_auto_click()
            self._pending_click_step = -1
            self.confirm_button.configure(state="normal")
            self.status.set(f"自动点击已停止：{error}")
            return
        self.root.after(550, lambda: self._verify_target_click(step, before, bbox))

    def _target_geometry(self) -> tuple[float, float, float] | None:
        if self.guide is None or self.guide.next_action is None:
            return None
        kind, value = self.guide.next_action
        if kind == "park":
            return (getattr(self, "_park_geometry", None)
                    if getattr(self, "_park_geometry_step", -1) == self.guide.confirmed else None)
        if kind != "board":
            return None
        col, row = self.positions[self.guide.board.ids[value]]
        cell = self.cell_px.get()
        x, y = tile_center(col, row, center_x=self.center_x.get(), center_y=self.center_y.get(),
                           cell_px=cell, mid_col=self.mid_col, mid_row=self.mid_row)
        if self._target_adjustment_step == self.guide.confirmed:
            x += self._target_adjustment[0]
            y += self._target_adjustment[1]
        return x, y, cell

    def _capture_target_snapshot(self) -> None:
        if not self.auto_verified or not self.calibrated or self.paused or self.pending_revive:
            return
        if self.guide is None or self.last_rect is None:
            return
        if self.background_click.get():
            hwnd, left, top, width, height = self.last_rect
            key = (self.capture_epoch, self.guide.confirmed, hwnd, width, height)
            try:
                self._grab_game_region((left, top, left + width, top + height))
                self._background_wait = None
            except OSError as error:
                waiting = getattr(self, "_background_wait", None)
                if waiting is None or waiting[0] != key:
                    waiting = self._background_wait = (key, time.monotonic())
                if time.monotonic() - waiting[1] < 3:
                    self.root.after(150, self._capture_target_snapshot)
                else:
                    self._disable_auto_click()
                    self.status.set(f"后台画面不可用，已停止模拟点击：{error}")
                return
        if self.guide.next_action is not None and self.guide.next_action[0] == "park":
            self._park_geometry_step = -1
            _hwnd, left, top, width, height = self.last_rect
            try:
                frame = self._grab_game_region((left, top, left + width, top + height))
                found = find_parked_cards(frame, self.cell_px.get(),
                                          tuple(bool(slot) for slot in self.guide.session.state.parked))
            except OSError:
                found = {}
            self._park_geometry = found.get(self.guide.next_action[1])
            if self._park_geometry is None:
                self._disable_auto_click()
                self.status.set("未能唯一定位移出区牌；请保持游戏可见，重新对齐或手动取回并确认")
                return
            self._park_geometry_step = self.guide.confirmed
        geometry = self._target_geometry()
        if geometry is None:
            return
        x, y, cell = geometry
        _hwnd, left, top, width, height = self.last_rect
        radius = max(8, round(cell * 0.25))
        if (x - radius < 0 or y - radius < 0
                or x + radius >= width or y + radius >= height):
            self._target_snapshot = None
            self._target_bbox = None
            self._snapshot_step = -1
            self._disable_auto_click()
            self.status.set("目标位置超出游戏区域，自动点击已停；请重新对齐")
            return
        search_half = max(radius + 3, round(cell * 0.68))
        search_bbox = (round(left + x) - search_half, round(top + y) - search_half,
                       round(left + x) + search_half, round(top + y) + search_half)
        try:
            search_image = self._grab_game_region(search_bbox)
            refined = find_exposed_card_center(search_image, search_half, search_half, cell)
        except OSError:
            refined = None
        if refined is not None:
            adjustment = (refined[0] - search_half, refined[1] - search_half)
            self._target_adjustment = adjustment
            self._target_adjustment_step = self.guide.confirmed
            x += adjustment[0]
            y += adjustment[1]
            if self.guide.next_action[0] == "park":
                self._park_geometry = (x, y, cell)
        screen_x, screen_y = round(left + x), round(top + y)
        bbox = (screen_x - radius, screen_y - radius,
                screen_x + radius, screen_y + radius)
        try:
            snapshot = self._grab_game_region(bbox).convert("L")
        except OSError:
            self._target_snapshot = None
            self._target_bbox = None
            self._snapshot_step = -1
            self._disable_auto_click()
            self.status.set("无法截图核对目标牌，自动点击已停；请检查游戏窗口")
            return
        self._target_snapshot = snapshot
        self._target_bbox = bbox
        self._snapshot_step = self.guide.confirmed

    def _poll_click(self) -> None:
        if self.closed.is_set():
            return
        user32 = ctypes.windll.user32
        mouse_down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        if mouse_down and not self._mouse_was_down and self.auto_verified and self.calibrated:
            geometry = self._target_geometry()
            game_foreground = (self.last_rect is not None
                               and user32.GetForegroundWindow() == self.last_rect[0])
            if self.auto_click_enabled and game_foreground and geometry is not None:
                point = POINT()
                user32.GetCursorPos(ctypes.byref(point))
                x, y, cell = geometry
                _hwnd, left, top, _width, _height = self.last_rect
                if (abs(point.x - left - x) > cell * 0.32
                        or abs(point.y - top - y) > cell * 0.32):
                    self._disable_auto_click()
                    self.status.set("检测到准心外点击，自动点击已关闭；若牌局改变请暂停重抓")
            if (geometry is not None and self.last_rect is not None and self.guide is not None
                    and self._target_snapshot is not None
                    and self._snapshot_step == self.guide.confirmed
                    and self._pending_click_step != self.guide.confirmed
                    and user32.GetForegroundWindow() == self.last_rect[0]):
                point = POINT()
                user32.GetCursorPos(ctypes.byref(point))
                x, y, cell = geometry
                _hwnd, left, top, _width, _height = self.last_rect
                if abs(point.x - left - x) <= cell * 0.32 and abs(point.y - top - y) <= cell * 0.32:
                    step = self.guide.confirmed
                    self._pending_click_step = step
                    before = self._target_snapshot
                    bbox = self._target_bbox
                    self.root.after(450, lambda: self._verify_target_click(step, before, bbox))
        self._mouse_was_down = mouse_down
        self.root.after(20, self._poll_click)

    def _verify_target_click(self, step: int, before, bbox) -> None:
        if (self.guide is None or self.guide.confirmed != step or self.paused
                or not self.auto_verified or bbox is None):
            return
        try:
            after = self._grab_game_region(bbox).convert("L")
            changed = (self.last_rect is not None
                       and (self.background_click.get() or
                            ctypes.windll.user32.GetForegroundWindow() == self.last_rect[0])
                       and visibly_changed(before, after))
        except OSError:
            changed = False
        if changed:
            self.auto_click_pending_step = -1
            self.last_auto_click = time.monotonic()
            self.status.set("已识别目标砖块的点击与画面变化，自动推进到下一步")
            self._confirm(visual_confirmed=True)
        else:
            self._pending_click_step = -1
            self.confirm_button.configure(state="normal" if self.calibrated and not self.paused else "disabled")
            if self.auto_click_enabled:
                self._disable_auto_click()
                self.status.set("自动点击已停止：目标画面变化未确认；请核对游戏后手动处理")
                return
            self.status.set("检测到目标区域点击，但未确认牌面变化；请核对后按「手动下一步」")

    def _draw_overlay(self, width: int, height: int) -> None:
        self.canvas.delete("all")
        if self.guide is None or self.paused or self.waiting_for_second or not self.face_verified:
            return
        try:
            center_x, center_y, cell = self.center_x.get(), self.center_y.get(), self.cell_px.get()
        except tk.TclError:
            return
        if cell <= 0:
            return
        action = self.guide.next_action
        if action is None:
            return
        if self.pending_revive:
            self.canvas.create_rectangle(10, 10, min(width - 10, 340), 50,
                                         fill="#262626", outline="#ff3030", width=2)
            self.canvas.create_text(175, 30, text="先完成复活移出，再按面板确认",
                                    fill="white", font=("Segoe UI", 12, "bold"))
            return
        kind, value = action
        if kind in ("board", "park"):
            geometry = self._target_geometry()
            if geometry is None:
                return
            x, y, cell = geometry
            radius = cell * 0.40
            arm = max(6, cell * 0.13)
            focus_color = ACCENT if self.calibrated else "#ffbd69"
            # Four open corners frame the tile without covering its face.
            for dx in (-1, 1):
                for dy in (-1, 1):
                    corner_x, corner_y = x + dx * radius, y + dy * radius
                    points = (corner_x - dx * arm, corner_y,
                              corner_x, corner_y,
                              corner_x, corner_y - dy * arm)
                    self.canvas.create_line(*points, fill="#10333c", width=5,
                                            capstyle=tk.ROUND, joinstyle=tk.ROUND)
                    self.canvas.create_line(*points, fill=focus_color, width=2,
                                            capstyle=tk.ROUND, joinstyle=tk.ROUND)
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3,
                                    fill="#10333c", outline=focus_color, width=1)
            chip_y = max(18, y - cell * 0.5 - 21)
            chip_x = min(max(x, 75), width - 75)
            self.canvas.create_oval(chip_x - 72, chip_y - 12, chip_x - 48, chip_y + 12,
                                    fill="#10333c", outline="")
            self.canvas.create_rectangle(chip_x - 60, chip_y - 12, chip_x + 60, chip_y + 12,
                                         fill="#10333c", outline="")
            self.canvas.create_oval(chip_x + 48, chip_y - 12, chip_x + 72, chip_y + 12,
                                    fill="#10333c", outline="")
            self.canvas.create_oval(chip_x - 61, chip_y - 3, chip_x - 55, chip_y + 3,
                                    fill=focus_color, outline="")
            self.canvas.create_text(chip_x + 4, chip_y,
                                    text=(f"下一张  {self.guide.confirmed + 1}/{len(self.guide.plan)}"
                                          if self.calibrated else "待校准 · 仅预览"),
                                    fill=TEXT, font=("Microsoft YaHei UI", 9, "bold"))
        else:
            label = ("待校准 · 仅预览" if not self.calibrated else
                     "点击移出三张道具" if kind == "move_out" else f"点击移出区第 {value + 1} 列")
            self.canvas.create_rectangle(10, 10, min(width - 10, 310), 48, fill="#262626", outline="#ff3030", width=2)
            self.canvas.create_text(160, 29, text=label, fill="white", font=("Segoe UI", 12, "bold"))

    def _tick(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind, payload = event[:2]
            if len(event) == 3 and event[2] != self.capture_epoch:
                continue
            if kind == "status":
                self.status.set(str(payload))
            elif kind == "capture_ready":
                self.capture_process, routed = payload if isinstance(payload, tuple) else (payload, False)
                if self.latest:
                    self.started_at = 0.0
                self.latest = False
                self.capture_button.configure(text="抓包中", state="disabled")
                self.confirm_button.configure(text="等待完整路线", state="disabled")
                if routed:
                    message = "抓包已就绪，系统代理已临时切换；请在游戏内开新局。若未收到请求，再重新打开小程序；第一关自行通关"
                elif self.started_at == 0.0:
                    message = "正在载入最近抓包；第一关请自行通关"
                else:
                    message = "等待外部抓包器数据；第一关请自行通关"
                self.status.set(message)
                threading.Thread(target=self._watch_capture, args=(self.capture_epoch,), daemon=True).start()
            elif kind == "captured":
                capture_error = self._finish_capture_recording()
                self.status.set(str(payload) if capture_error is None
                                else f"地图已抓到，但未能停止记录：{capture_error}")
            elif kind == "capture_error":
                self.capture_button.configure(text="重试抓包", state="normal")
                self.latest_button.configure(state="normal")
                self.route_badge.set("抓包未启动")
                self.status.set(f"抓包未启动：{payload}")
            elif kind in ("alignment", "alignment_error"):
                self.calibration_pending = False
                if (kind == "alignment" and isinstance(payload, Alignment)
                        and self._alignment_in_bounds(payload)):
                    self.center_x.set(payload.center_x)
                    self.center_y.set(payload.center_y)
                    self.cell_px.set(payload.cell_px)
                    if not self.face_verified:
                        self.calibrated = False
                        self.auto_verified = False
                        self.status.set("地图位置已找到，但每日牌面尚未实测核对；点击标记继续隐藏")
                    else:
                        self.calibrated = True
                        self.auto_verified = True
                        try:
                            self._save_calibration()
                        except (OSError, ValueError) as error:
                            self.calibrated = False
                            self.auto_verified = False
                            self.status.set(f"自动对齐未保存：{error}；请重新对齐")
                        else:
                            self.status.set(f"已生成{self.target_label}准心；请核对位置，再按需开启自动点击")
                else:
                    self.calibrated = False
                    self.auto_verified = False
                    detail = f"：{payload}" if kind == "alignment_error" else ""
                    self.status.set("每日关卡牌面未与实测核对；已隐藏标记和点击"
                                    if not self.face_verified else
                                    f"自动对齐未确认{detail}；保留橙色预览准心供微调，校准前不会点击")
                self._update_step()
            elif kind == "new_game":
                self.guide = None
                self._update_progress()
                self.route_badge.set("正在求解")
                self._disable_auto_click()
                self.positions = {}
                self.calibrated = False
                self.waiting_for_second = False
                self.pending_revive = False
                self.paused = False
                self.auto_verified = False
                self.confirm_button.configure(state="disabled", text="等待完整路线")
                self.special_button.configure(state="disabled", text="道具或复活完成后在这里确认")
                self.calibrate_button.configure(state="normal")
                self.auto_button.configure(state="disabled", text="⟳ 重新自动对齐")
                self.auto_click_button.configure(state="disabled")
                self.step_text.set("正在等待新局的完整路线")
                self.status.set(str(payload))
            elif kind == "unsolved":
                self._reset_capture_cycle(str(payload))
                messagebox.showinfo("建议重开关卡", self.status.get())
            elif kind == "capture_stopped":
                self._reset_capture_cycle(str(payload))
            else:
                board, plan, cards_path, progress_path, wait_for_second, *label = payload
                try:
                    self._set_guide(board, plan, cards_path, progress_path, wait_for_second,
                                    label[0] if label else "第二关")
                    self.capture_button.configure(text="新局抓包", state="normal")
                    self.latest_button.configure(state="normal")
                except (OSError, ValueError) as error:
                    self.status.set(f"路线载入失败：{error}")
                    self.capture_button.configure(text="新局抓包", state="normal")
                    self.latest_button.configure(state="normal")
        rect = game_client_rect()
        if rect is None:
            self.overlay.withdraw()
            if self.last_rect is not None:
                self._disable_auto_click()
                self.calibrated = False
                self.auto_verified = False
                self.calibration_attempted = False
                self._update_step()
            self.last_rect = None
        else:
            hwnd, x, y, width, height = rect
            if rect != self.last_rect:
                previous = self.last_rect
                self.last_rect = rect
                if previous is None:
                    self._apply_saved_calibration(width, height)
                elif (width, height) != (previous[3], previous[4]):
                    scale_x = width / previous[3]
                    scale_y = height / previous[4]
                    self.center_x.set(self.center_x.get() * scale_x)
                    self.center_y.set(self.center_y.get() * scale_y)
                    self.cell_px.set(self.cell_px.get() * scale_x)
                    self.calibrated = False
                    self.auto_verified = False
                    self.calibration_attempted = False
                    self._update_step()
                self.overlay.geometry(f"{width}x{height}+{x}+{y}")
                self.canvas.configure(width=width, height=height)
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            user32.GetForegroundWindow.restype = wintypes.HWND
            foreground = user32.GetForegroundWindow()
            panel_hwnd = user32.GetAncestor(self.root.winfo_id(), 2)
            if (foreground in (hwnd, panel_hwnd) and self.guide is not None and not self.paused
                    and not self.calibration_pending and not self.waiting_for_second and self.face_verified):
                self.overlay.deiconify()
                self.overlay.lift()
                try:
                    click_through = self._make_click_through()
                except Exception:  # A failed Win32 call must never leave a blocking overlay visible.
                    click_through = False
                if click_through:
                    self._draw_overlay(width, height)
                    if foreground == hwnd and not self.background_click.get():
                        self._maybe_auto_click(user32, hwnd, x, y, width, height)
                else:
                    self.overlay.withdraw()
                    self.status.set("标记层无法点击穿透，已隐藏；游戏仍可正常点击")
            else:
                self.overlay.withdraw()
            if self.background_click.get():
                self._maybe_auto_click(user32, hwnd, x, y, width, height)
        if (self.guide is not None and self.last_rect is not None and not self.paused
                and not self.waiting_for_second):
            if not self.calibration_attempted and not self.calibration_pending:
                self.root.after(0, self._request_auto_calibration)
        if not self.closed.is_set():
            self.root.after(150, self._tick)

    def close(self) -> None:
        self.closed.set()
        if self.background_frames is not None:
            self.background_frames.close()
        try:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            (self.capture_dir / CAPTURE_DONE_MARKER).touch()
        except OSError:
            pass
        try:
            self.capture_proxy.restore()
        except (OSError, ValueError) as error:
            messagebox.showerror("系统代理恢复失败", f"请手动将 Windows 代理恢复为抓包前的 Clash 地址（7890 或 7897 端口）：{error}")
        self.overlay.destroy()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_guide(**kwargs) -> None:
    GuideApp(**kwargs).run()
