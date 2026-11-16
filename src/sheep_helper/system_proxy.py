"""Temporarily route a fresh game through mitmproxy, then restore Windows proxy.

The saved setting and a small watchdog cover GUI crashes as well as normal
completion.  This module only changes the known Clash -> capture route; it
never overwrites a proxy configuration that differs from the expected one.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import winreg


INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
UPSTREAM_PROXY = "127.0.0.1:7890"
SUPPORTED_UPSTREAMS = (UPSTREAM_PROXY, "127.0.0.1:7897")
CAPTURE_PROXY = "127.0.0.1:18888"
STATE_NAME = ".system-proxy-restore.json"


def _read_proxy() -> tuple[int, str]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS) as key:
        enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
        server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
    return enabled, server


def _refresh_proxy() -> None:
    wininet = ctypes.windll.wininet
    for option in (39, 37):  # INTERNET_OPTION_SETTINGS_CHANGED / REFRESH
        if not wininet.InternetSetOptionW(None, option, None, 0):
            raise OSError("Windows 未能刷新系统代理设置")


def _write_proxy(enabled: int, server: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, enabled)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
    _refresh_proxy()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, 0, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x00000102
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def _record(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("owner_pid")) is not int
            or data.get("capture_proxy") != CAPTURE_PROXY
            or data.get("upstream_proxy") not in SUPPORTED_UPSTREAMS
            or data.get("original_enabled") != 1
            or data.get("original_server") != data.get("upstream_proxy")):
        raise ValueError("系统代理恢复记录无效；未改动现有代理")
    return data


def restore_proxy(path: Path, *, owner_pid: int | None = None) -> bool:
    """Restore only our saved proxy, and never overwrite later user changes."""
    if not path.is_file():
        return False
    data = _record(path)
    if owner_pid is not None and data["owner_pid"] != owner_pid:
        return False
    current = _read_proxy()
    if current == (1, CAPTURE_PROXY):
        _write_proxy(int(data["original_enabled"]), str(data["original_server"]))
    path.unlink()
    return True


def recover_stale_proxy(path: Path) -> bool:
    if not path.is_file():
        return False
    data = _record(path)
    if _pid_alive(int(data["owner_pid"])):
        return False
    return restore_proxy(path, owner_pid=int(data["owner_pid"]))


def watch_owner(path: Path, owner_pid: int) -> None:
    """Child process: restore after an unexpected GUI exit."""
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, 0, owner_pid)
    if handle:
        try:
            kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0xFFFFFFFF)
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        restore_proxy(path, owner_pid=owner_pid)
    except (OSError, ValueError):
        pass  # Keep the recovery record for the next GUI launch.


def start_proxy_watchdog(path: Path, owner_pid: int) -> subprocess.Popen:
    command = ([sys.executable] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "sheep_helper.desktop"])
    command.extend(("--proxy-watchdog", str(path), "--owner-pid", str(owner_pid)))
    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        # An independent watcher must not lock its owner's one-file resources.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)


class CaptureProxy:
    def __init__(self, capture_dir: Path) -> None:
        self.path = capture_dir / STATE_NAME
        self.owner_pid = os.getpid()

    def activate(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("自动代理切换只支持 Windows")
        recover_stale_proxy(self.path)
        if self.path.exists():
            raise RuntimeError("另一助手仍持有临时代理；请先关闭旧助手")
        original_enabled, original_server = _read_proxy()
        if original_enabled != 1 or original_server not in SUPPORTED_UPSTREAMS:
            raise RuntimeError("系统代理须为 Clash 127.0.0.1:7890 或 127.0.0.1:7897；未自动改动")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"owner_pid": self.owner_pid, "original_enabled": original_enabled,
                  "original_server": original_server, "capture_proxy": CAPTURE_PROXY,
                  "upstream_proxy": original_server}
        temporary = self.path.with_name(f"{STATE_NAME}.{self.owner_pid}.tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(self.path)
        try:
            start_proxy_watchdog(self.path, self.owner_pid)
            _write_proxy(1, CAPTURE_PROXY)
        except (OSError, ValueError):
            restore_proxy(self.path, owner_pid=self.owner_pid)
            raise

    def restore(self) -> bool:
        return restore_proxy(self.path, owner_pid=self.owner_pid)
