"""Read-only WMPF layout probe used before enabling a local debugger bridge."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

import frida


ROOT = Path(__file__).resolve().parents[1]
DETECTOR = ROOT / "vendor" / "WMPFDebugger" / "frida" / "autodetect" / "win32.js"


def find_wmpf_parent(device: frida.core.Device) -> tuple[int, int]:
    processes = [
        process
        for process in device.enumerate_processes(scope="metadata")
        if process.name == "WeChatAppEx.exe"
    ]
    parent_ids = [int(process.parameters.get("ppid", 0)) for process in processes]
    parent_pid, _count = Counter(parent_ids).most_common(1)[0]
    parent = next(process for process in processes if process.pid == parent_pid)
    argv = parent.parameters.get("argv", [])
    runtime_arg = next(arg for arg in argv if arg.startswith("--flue-runtime-dir"))
    version = int(re.findall(r"\d+", runtime_arg)[-1])
    return parent_pid, version


def main() -> None:
    device = frida.get_local_device()
    pid, version = find_wmpf_parent(device)
    session = device.attach(pid)
    result: dict[str, object] = {}

    def on_message(message: dict[str, object], _data: bytes | None) -> None:
        if message["type"] == "send":
            payload = message.get("payload", {})
            if isinstance(payload, dict):
                result.update(payload)
        else:
            result["error"] = message

    script = session.create_script(DETECTOR.read_text(encoding="utf-8"))
    script.on("message", on_message)
    try:
        script.load()
        deadline = time.monotonic() + 30
        while "type" not in result and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        script.unload()
        session.detach()

    print(json.dumps({"pid": pid, "version": version, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
