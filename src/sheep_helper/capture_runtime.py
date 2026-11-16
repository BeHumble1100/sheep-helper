"""Keep long-lived capture addons outside a one-file application's temp tree."""

import hashlib
from pathlib import Path
import tempfile


ADDON_FILES = (
    "__init__.py", "extractor.py", "game_capture_addon.py", "seed_recovery_addon.py",
    "redaction.py", "capture_state.py", "match_data.py",
)


def prepare_capture_runtime(source: Path, destination: Path) -> Path:
    files = {name: (source / name).read_bytes() for name in ADDON_FILES}
    digest = hashlib.sha256()
    for name, content in files.items():
        digest.update(name.encode("utf8") + b"\0" + content)
    package = destination / digest.hexdigest() / "sheep_helper"
    package.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = package / name
        if target.is_file() and target.read_bytes() == content:
            continue
        # Concurrent starts and interrupted writes must never expose half a module.
        with tempfile.NamedTemporaryFile(dir=package, delete=False) as temporary:
            temporary.write(content)
            staged = Path(temporary.name)
        try:
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)
    return package
