"""Regression checks for the Windows game overlay's mouse passthrough."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import tkinter as tk
import unittest

from sheep_helper.guide_ui import GuideApp, POINT


@unittest.skipUnless(sys.platform == "win32", "Windows overlay only")
class OverlayTests(unittest.TestCase):
    def test_click_through_style_is_on_outer_window(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            overlay = tk.Toplevel(root)
            overlay.overrideredirect(True)
            overlay.update_idletasks()
            app = object.__new__(GuideApp)
            app.overlay = overlay
            self.assertTrue(app._make_click_through())

            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            wrapper = user32.GetAncestor(overlay.winfo_id(), 2)
            self.assertNotEqual(wrapper, overlay.winfo_id())
            self.assertTrue(user32.GetWindowLongPtrW(wrapper, -20) & 0x20)
        finally:
            root.destroy()

    def test_opaque_overlay_pixel_does_not_win_mouse_hit_test(self) -> None:
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = (POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        x = user32.GetSystemMetrics(0) - 180
        y = user32.GetSystemMetrics(1) - 140
        root = tk.Tk()
        try:
            root.geometry(f"120x80+{x}+{y}")
            overlay = tk.Toplevel(root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.wm_attributes("-transparentcolor", "#010203")
            overlay.geometry(f"120x80+{x}+{y}")
            canvas = tk.Canvas(overlay, bg="#010203", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.create_rectangle(0, 0, 120, 80, fill="#e02020")
            overlay.lift()
            root.update()
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            overlay_wrapper = user32.GetAncestor(overlay.winfo_id(), 2)
            point = POINT(overlay.winfo_rootx() + 60, overlay.winfo_rooty() + 40)
            self.assertEqual(user32.GetAncestor(user32.WindowFromPoint(point), 2),
                             overlay_wrapper)
            app = object.__new__(GuideApp)
            app.overlay = overlay
            self.assertTrue(app._make_click_through())
            root.update()
            target = user32.WindowFromPoint(point)
            self.assertNotEqual(user32.GetAncestor(target, 2), overlay_wrapper)
        finally:
            root.destroy()
