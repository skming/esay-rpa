from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core import storage

_CONFIG_FILENAME = "notifications.json"


def _mask_secret(value: str) -> str:
    """前4位+****+后4位，过短则整体替换为 ****。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


class NotificationConfigService:
    def __init__(self, app_data_dir: str | None = None) -> None:
        self._path = (
            storage.resolve_app_data_dir() / "notifications" / _CONFIG_FILENAME
            if app_data_dir is None
            else Path(app_data_dir) / "notifications" / _CONFIG_FILENAME
        )

    def _default(self) -> dict[str, Any]:
        return {
            "dingtalk_enabled": False,
            "dingtalk_webhook_url": "",
            "dingtalk_secret": "",
        }

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
        """未脱敏，仅供内部使用（如 notifier）。"""
        return self._read_file()

    def save(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self._read_file()

        if "dingtalk_enabled" in patch and isinstance(patch["dingtalk_enabled"], bool):
            current["dingtalk_enabled"] = patch["dingtalk_enabled"]

        if "dingtalk_webhook_url" in patch and isinstance(patch["dingtalk_webhook_url"], str):
            current["dingtalk_webhook_url"] = patch["dingtalk_webhook_url"].strip()

        if "dingtalk_secret" in patch and isinstance(patch["dingtalk_secret"], str):
            stripped = patch["dingtalk_secret"].strip()
            # 前端回显的是脱敏值（含 ****），原样传回视为"未修改"，避免把脱敏串当新密钥存入。
            if stripped and "****" not in stripped:
                current["dingtalk_secret"] = stripped
            elif stripped == "":
                current["dingtalk_secret"] = ""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return current

    def get_masked_config(self) -> dict[str, Any]:
        config = self._read_file()
        return {
            "dingtalk_enabled": config["dingtalk_enabled"],
            "dingtalk_webhook_url": config["dingtalk_webhook_url"],
            "dingtalk_secret": _mask_secret(config["dingtalk_secret"]),
        }
