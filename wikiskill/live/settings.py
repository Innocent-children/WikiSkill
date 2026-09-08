from __future__ import annotations

import json
from dataclasses import asdict

from wikiskill.storage import atomic_text

from .config import Config
from .skills import file_lock


def public_settings(config: Config) -> dict:
    values = asdict(config)
    values.pop("root")
    values["api_key_configured"] = bool(values.pop("api_key"))
    return values


def save_settings(root, values: dict) -> dict:
    if not isinstance(values, dict):
        raise ValueError("Settings must be an object")
    allowed = set(Config.__dataclass_fields__) - {"root"}
    if set(values) - allowed - {"clear_api_key"}:
        raise ValueError("Unknown settings fields")
    with file_lock(root / "locks" / "settings.lock"):
        current = Config.load(root)
        changes = dict(values)
        clear = changes.pop("clear_api_key", False)
        if type(clear) is not bool:
            raise ValueError("clear_api_key must be boolean")
        if changes.get("api_key") == "":
            changes.pop("api_key")
        if clear:
            changes["api_key"] = ""
        config = Config(**{**asdict(current), **changes})
        data = asdict(config)
        data.pop("root")
        atomic_text(root / "config.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        (root / "config.json").chmod(0o600)
    return public_settings(config)
