"""What has been done, so nothing is clipped twice. Kept in ~/rippleclips-state.json on the droplet."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config


def load(path: Path | None = None) -> dict:
    path = path or config.STATE_FILE
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"videos": {}}


def save(state: dict, path: Path | None = None) -> None:
    path = path or config.STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)


def is_done(state: dict, file_id: str) -> bool:
    return state["videos"].get(file_id, {}).get("status") == "done"


def failures(state: dict, file_id: str) -> int:
    return int(state["videos"].get(file_id, {}).get("failures", 0))
