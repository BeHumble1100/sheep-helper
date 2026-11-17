import ctypes
from ctypes import wintypes
import sys
import time
import tkinter as tk
import unittest

from sheep_helper.background import BackgroundFrames, capture_client, post_click
from PIL import Image


class Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


@unittest.skipUnless(sys.platform == "win32", "Windows window messages")
class BackgroundTests(unittest.TestCase):
    def test_cached_frame_must_be_newer_than_current_step(self):
        frames = BackgroundFrames.__new__(BackgroundFrames)
        target = (1, 100, 200)
        image = Image.new("RGB", (100, 200))
        frames.error = None
        now = time.monotonic()
        frames.latest = (target, now, image)
        self.assertIs(frames.frame(target, not_before=now - 0.1), image)
        with self.assertRaises(OSError):
            frames.frame(target, not_before=now + 0.1)

    def test_message_click_reaches_child_without_moving_cursor_or_focus(self):
        root = tk.Tk()
        try:
            root.geometry("180x130+30+70")
            canvas = tk.Canvas(root, bg="#1133ee", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            root.update()
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(root.winfo_id(), 2)
            # Observe the native message queue itself: Tk routes its own mouse
            # bindings via the global cursor, unlike the embedded game renderer.
            messages = []
            procedure_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                                wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                               wintypes.WPARAM, wintypes.LPARAM]
            user32.CallWindowProcW.restype = ctypes.c_ssize_t
            original = None
            def observe(window, message, buttons, coordinates):
                if message in (0x0201, 0x0202):
                    messages.append((message, coordinates & 0xffff, (coordinates >> 16) & 0xffff))
                return user32.CallWindowProcW(original, window, message, buttons, coordinates)
            procedure = procedure_type(observe)
            original = user32.SetWindowLongPtrW(canvas.winfo_id(), -4,
                                                ctypes.cast(procedure, ctypes.c_void_p).value)
            before = Point()
            user32.GetCursorPos(ctypes.byref(before))
            foreground = user32.GetForegroundWindow()
            post_click(hwnd, 50, 60)
            for _ in range(5):
                root.update()
                time.sleep(0.02)
            after = Point()
            user32.GetCursorPos(ctypes.byref(after))
            self.assertEqual(messages, [(0x0201, 50, 60), (0x0202, 50, 60)])
            self.assertEqual((before.x, before.y), (after.x, after.y))
            self.assertEqual(user32.GetForegroundWindow(), foreground)
            user32.SetWindowLongPtrW(canvas.winfo_id(), -4, original)
        finally:
            root.destroy()

    def test_window_capture_reads_target_behind_another_window(self):
        root = tk.Tk()
        cover = None
        try:
            root.geometry("180x130+30+70")
            tk.Canvas(root, bg="#1133ee", highlightthickness=0).pack(fill="both", expand=True)
            root.update()
            cover = tk.Toplevel(root)
            cover.overrideredirect(True)
            cover.geometry(f"180x130+{root.winfo_rootx()}+{root.winfo_rooty()}")
            cover.configure(bg="#ee2211")
            cover.lift()
            root.update()
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(root.winfo_id(), 2)
            image = capture_client(hwnd, 180, 130)
            red, green, blue = image.getpixel((60, 60))
            self.assertGreater(blue, 200)
            self.assertLess(red, 80)
        finally:
            if cover is not None:
                cover.destroy()
            root.destroy()
