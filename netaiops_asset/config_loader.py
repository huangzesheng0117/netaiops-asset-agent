import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = os.getenv("NETAIOPS_CONFIG", "/etc/netaiops-asset-agent/config.yaml")


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    path = Path(CONFIG_PATH)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def reload_config() -> dict[str, Any]:
    get_config.cache_clear()
    return get_config()
