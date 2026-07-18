from __future__ import annotations

import json
from pathlib import Path

from .paths import app_dir

CONFIG_PATH = app_dir() / "settings.json"

DEFAULTS = {
    "host": "192.168.1.11",
    "fog_port": 2121,
}


def load() -> dict:
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update({k: saved[k] for k in DEFAULTS if k in saved})
                # migrate old "port" key
                if "fog_port" not in saved and "port" in saved:
                    data["fog_port"] = saved["port"]
                if "host" in saved:
                    data["host"] = saved["host"]
                # old FTP default — always use FogTransfer port
                if int(data.get("fog_port", 2121)) in (21, 1337):
                    data["fog_port"] = 2121
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return data


def save(data: dict) -> None:
    out = {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS}
    CONFIG_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
