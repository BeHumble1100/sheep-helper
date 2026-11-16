from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from sheep_helper.capture_runtime import ADDON_FILES, prepare_capture_runtime


SOURCE = Path(__file__).resolve().parents[1] / "src/sheep_helper"


class CaptureRuntimeTests(unittest.TestCase):
    def test_addons_can_import_after_extracted_sources_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extracted = root / "_MEI-test" / "sheep_helper"
            extracted.mkdir(parents=True)
            for name in ADDON_FILES:
                shutil.copyfile(SOURCE / name, extracted / name)
            package = prepare_capture_runtime(extracted, root / "runtime")
            # Resolve and verify both targets before removing our own test tree.
            self.assertTrue(extracted.resolve().is_relative_to(root.resolve()))
            shutil.rmtree(extracted.parent)
            script = ("import sys;sys.path.insert(0,sys.argv[1]);"
                      "import sheep_helper.game_capture_addon as a;"
                      "import sheep_helper.seed_recovery_addon;"
                      "print(a.__file__)")
            result = subprocess.run([sys.executable, "-c", script, str(package.parent)],
                                    check=True, capture_output=True, text=True)
            self.assertIn(str(package), result.stdout)

    def test_changed_addons_get_a_new_runtime_without_changing_existing_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            for name in ADDON_FILES:
                shutil.copyfile(SOURCE / name, source / name)
            first = prepare_capture_runtime(source, root / "runtime")
            original = (first / "game_capture_addon.py").read_bytes()
            (source / "game_capture_addon.py").write_bytes(original + b"\n# updated\n")
            second = prepare_capture_runtime(source, root / "runtime")
            self.assertNotEqual(first, second)
            self.assertEqual((first / "game_capture_addon.py").read_bytes(), original)
            self.assertEqual(prepare_capture_runtime(source, root / "runtime"), second)
