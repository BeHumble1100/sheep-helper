"""Create a reversible Clash profile that routes only the mini-program process."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from urllib.request import Request, urlopen

from ruamel.yaml import YAML

CAPTURE_PROXY = "SHEEP_HELPER_CAPTURE"
CAPTURE_RULE = "PROCESS-NAME,WeChatAppEx.exe,SHEEP_HELPER_CAPTURE"


def make_capture_profile(source: Path, destination: Path, port: int = 18888) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    with source.open("r", encoding="utf-8") as stream:
        document = yaml.load(stream)
    if not isinstance(document, dict):
        raise ValueError("Clash 配置根节点必须是映射")
    document = deepcopy(document)
    proxies = list(document.get("proxies") or [])
    proxies = [proxy for proxy in proxies if proxy.get("name") != CAPTURE_PROXY]
    proxies.insert(0, {"name": CAPTURE_PROXY, "type": "http", "server": "127.0.0.1", "port": port, "udp": False})
    document["proxies"] = proxies
    rules = [rule for rule in list(document.get("rules") or []) if rule != CAPTURE_RULE]
    document["rules"] = [CAPTURE_RULE, *rules]
    with destination.open("w", encoding="utf-8") as stream:
        yaml.dump(document, stream)


def reload_profile(base_config: Path, profile: Path) -> None:
    """Ask the local Clash controller to load a profile; never prints its secret."""
    yaml = YAML(typ="safe")
    with base_config.open("r", encoding="utf-8") as stream:
        settings = yaml.load(stream)
    controller = settings["external-controller"]
    request = Request(
        f"http://{controller}/configs?force=true",
        data=json.dumps({"path": str(profile), "payload": ""}).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings['secret']}", "Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(request, timeout=10) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Clash 配置重载失败：HTTP {response.status}")
