from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from sheep_helper.system_proxy import CAPTURE_PROXY, UPSTREAM_PROXY, CaptureProxy, recover_stale_proxy


class SystemProxyTests(unittest.TestCase):
    def test_frozen_watchdog_does_not_reuse_the_owner_extraction_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CaptureProxy(Path(directory))
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, UPSTREAM_PROXY)), patch(
                    "sheep_helper.system_proxy._write_proxy"), patch(
                    "sheep_helper.system_proxy.sys.frozen", True, create=True), patch.dict(
                    os.environ, {"PYINSTALLER_RESET_ENVIRONMENT": "0"}), patch(
                    "sheep_helper.system_proxy.subprocess.Popen") as launch:
                manager.activate()
                self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
                self.assertEqual(os.environ["PYINSTALLER_RESET_ENVIRONMENT"], "0")

    def test_port_7897_is_restored_after_owner_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = CaptureProxy(Path(directory))
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, "127.0.0.1:7897")), patch(
                    "sheep_helper.system_proxy._write_proxy"), patch(
                    "sheep_helper.system_proxy.subprocess.Popen"):
                manager.activate()
            with patch("sheep_helper.system_proxy._pid_alive", return_value=False), patch(
                    "sheep_helper.system_proxy._read_proxy", return_value=(1, CAPTURE_PROXY)), patch(
                    "sheep_helper.system_proxy._write_proxy") as restore:
                self.assertTrue(recover_stale_proxy(manager.path))
            restore.assert_called_once_with(1, "127.0.0.1:7897")
            self.assertFalse(manager.path.exists())

    def test_activate_and_restore_preserves_original_setting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = CaptureProxy(Path(directory))
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, UPSTREAM_PROXY)), patch(
                    "sheep_helper.system_proxy._write_proxy") as write, patch(
                    "sheep_helper.system_proxy.subprocess.Popen") as launch:
                manager.activate()
            self.assertTrue(manager.path.is_file())
            write.assert_called_once_with(1, CAPTURE_PROXY)
            self.assertIn("--proxy-watchdog", launch.call_args.args[0])
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, CAPTURE_PROXY)), patch(
                    "sheep_helper.system_proxy._write_proxy") as restore:
                self.assertTrue(manager.restore())
            restore.assert_called_once_with(1, UPSTREAM_PROXY)
            self.assertFalse(manager.path.exists())

    def test_does_not_override_unknown_proxy_or_later_user_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = CaptureProxy(Path(directory))
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, "other:8080")), patch(
                    "sheep_helper.system_proxy._write_proxy") as write:
                with self.assertRaisesRegex(RuntimeError, "未自动改动"):
                    manager.activate()
                write.assert_not_called()
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, UPSTREAM_PROXY)), patch(
                    "sheep_helper.system_proxy._write_proxy"), patch(
                    "sheep_helper.system_proxy.subprocess.Popen"):
                manager.activate()
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, "other:8080")), patch(
                    "sheep_helper.system_proxy._write_proxy") as write:
                self.assertTrue(manager.restore())
                write.assert_not_called()

    def test_stale_record_recovers_after_owner_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = CaptureProxy(Path(directory))
            with patch("sheep_helper.system_proxy._read_proxy", return_value=(1, UPSTREAM_PROXY)), patch(
                    "sheep_helper.system_proxy._write_proxy"), patch(
                    "sheep_helper.system_proxy.subprocess.Popen"):
                manager.activate()
            with patch("sheep_helper.system_proxy._pid_alive", return_value=False), patch(
                    "sheep_helper.system_proxy._read_proxy", return_value=(1, CAPTURE_PROXY)), patch(
                    "sheep_helper.system_proxy._write_proxy") as restore:
                self.assertTrue(recover_stale_proxy(manager.path))
            restore.assert_called_once_with(1, UPSTREAM_PROXY)


if __name__ == "__main__":
    unittest.main()
