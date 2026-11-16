"""Window-local input and captures; neither operation moves the desktop cursor."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time

from PIL import Image


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG),
                ("height", wintypes.LONG), ("planes", wintypes.WORD),
                ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                ("image_size", wintypes.DWORD), ("xppm", wintypes.LONG),
                ("yppm", wintypes.LONG), ("used", wintypes.DWORD),
                ("important", wintypes.DWORD), ("colors", wintypes.DWORD * 3)]


def capture_client(hwnd: int, width: int, height: int) -> Image.Image:
    """PrintWindow can block: callers must use a worker, never the GUI thread."""
    if width <= 0 or height <= 0 or width * height > 25_000_000:
        raise OSError("invalid window size")
    user32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        raise OSError("game window is unavailable or minimized")
    user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi.CreateDIBSection.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    dc = user32.GetDC(hwnd)
    memory = bitmap = previous = None
    try:
        memory = gdi.CreateCompatibleDC(dc)
        info = _BitmapInfo()
        info.size, info.width, info.height = 40, width, -height
        info.planes, info.bits = 1, 32
        pixels = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(pixels), None, 0)
        if not memory or not bitmap or not pixels.value:
            raise OSError("cannot allocate window capture")
        previous = gdi.SelectObject(memory, bitmap)
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT supports the embedded renderer.
        if not user32.PrintWindow(hwnd, memory, 3):
            raise OSError("window did not supply a background frame")
        raw = ctypes.string_at(pixels, width * height * 4)
        image = Image.frombytes("RGB", (width, height), raw, "raw", "BGRX")
        if max(high - low for low, high in image.getextrema()) < 8:
            raise OSError("window returned an empty background frame")
        return image
    finally:
        if previous and memory:
            gdi.SelectObject(memory, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory:
            gdi.DeleteDC(memory)
        if dc:
            user32.ReleaseDC(hwnd, dc)


def post_click(hwnd: int, x: int, y: int) -> None:
    """Deliver a client-coordinate click to the renderer child under the point."""
    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, _Point, wintypes.UINT]
    user32.ChildWindowFromPointEx.restype = wintypes.HWND
    user32.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        raise OSError("game window is unavailable or minimized")
    target, point = hwnd, _Point(x, y)
    for _ in range(16):
        child = user32.ChildWindowFromPointEx(target, point, 7)
        if not child or child == target:
            break
        user32.MapWindowPoints(target, child, ctypes.byref(point), 1)
        target = child
    if not (0 <= point.x <= 32767 and 0 <= point.y <= 32767):
        raise OSError("click coordinate is outside the renderer")
    packed = point.x | (point.y << 16)
    for message, buttons in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
        if not user32.PostMessageW(target, message, buttons, packed):
            raise OSError("window rejected simulated click messages")


class BackgroundFrames:
    """One daemon worker bounds hung PrintWindow calls to one thread."""

    def __init__(self) -> None:
        self.target = None
        self.latest = None
        self.error = None
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def request(self, target: tuple[int, int, int]) -> None:
        self.target = target
        self.wake.set()

    def frame(self, target: tuple[int, int, int]) -> Image.Image:
        item = self.latest
        if item is None or item[0] != target or time.monotonic() - item[1] > 0.8:
            raise OSError(self.error or "background frame is not ready")
        return item[2]

    def _run(self) -> None:
        while not self.stopped.is_set():
            self.wake.wait(0.2)
            self.wake.clear()
            target = self.target
            if target is None:
                continue
            try:
                image = capture_client(*target)
                self.latest = (target, time.monotonic(), image)
                self.error = None
            except OSError as error:
                self.latest = None
                self.error = str(error)
            self.stopped.wait(0.2)

    def close(self) -> None:
        self.stopped.set()
        self.wake.set()
