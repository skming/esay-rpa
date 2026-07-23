from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core import storage

_CONFIG_FILENAME = "extension.json"


class ExtensionConfigService:
    """用户级开关，独立于 `ExtensionBridgeService.is_connected` 的实时连接状态——
    即使 Chrome 已连接，也可用它把扩展选项从运行对话框中隐藏。"""

    def __init__(self, app_data_dir: str | None = None) -> None:
        self._path = (
            storage.resolve_app_data_dir() / "extension" / _CONFIG_FILENAME
            if app_data_dir is None
            else Path(app_data_dir) / "extension" / _CONFIG_FILENAME
        )

    def _default(self) -> dict[str, Any]:
        return {"enabled": True}

    def _read_file(self) -> dict[str, Any]:
        default = self._default()
        if not self._path.exists():
            return default
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return default
            default.update({k: data[k] for k in default if k in data})
            return default
        except Exception:
            return default

    def load(self) -> dict[str, Any]:
        return self._read_file()

    def save(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self._read_file()
        if "enabled" in patch and isinstance(patch["enabled"], bool):
            current["enabled"] = patch["enabled"]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return current
