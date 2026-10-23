"""Attach the verified WMPF hook and keep it alive for one local session."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import frida

from probe_wmpf_offsets import DETECTOR, find_wmpf_parent


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "vendor" / "WMPFDebugger" / "frida" / "hook.js"


def detect(session: frida.core.Session) -> dict[str, object]:
    result: dict[str, object] = {}
    probe = session.create_script(DETECTOR.read_text(encoding="utf-8"))
    probe.on(
        "message",
        lambda message, _data: result.update(message.get("payload", {}))
        if message["type"] == "send" and isinstance(message.get("payload"), dict)
        else result.update({"error": message}),
    )
    try:
        probe.load()
        deadline = time.monotonic() + 30
        while "type" not in result and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        probe.unload()
    if result.get("type") != "wmpf-offsets":
        raise RuntimeError(f"偏移探测失败：{result}")
    return result["config"]  # type: ignore[return-value]


def main() -> None:
    device = frida.get_local_device()
    pid, version = find_wmpf_parent(device)
    session = device.attach(pid)
    try:
        config = {"Version": version, **detect(session)}
        source = HOOK.read_text(encoding="utf-8").replace("@@CONFIG@@", json.dumps(config))
        hook = session.create_script(source)
        hook.on("message", lambda message, _data: print(json.dumps(message, ensure_ascii=False)))
        hook.load()
        print(json.dumps({"status": "hook-loaded", "pid": pid, "version": version}))
        print("等待重新打开小游戏；按 Ctrl+C 停止并脱离。", flush=True)
        threading.Event().wait()
    finally:
        session.detach()


if __name__ == "__main__":
    main()
