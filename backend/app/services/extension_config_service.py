from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from app.core import storage

_CONFIG_FILENAME = "extension.json"


class ExtensionConfigService:
    """用户级开关，独立于 `ExtensionBridgeService.is_connected` 的实时连接状态——
    即使 Chrome 已连接，也可用它把扩展选项从运行对话框中隐藏。"""

    def __init__(self, app_data_dir: str | None = None, extension_manifest_path: Path | None = None) -> None:
        directory = (storage.resolve_app_data_dir() if app_data_dir is None else Path(app_data_dir)) / "extension"
        self._path = directory / _CONFIG_FILENAME
        resources_root = Path(__file__).resolve().parents[3]
        self._manifest_paths = [extension_manifest_path] if extension_manifest_path is not None else [
            resources_root / "extension" / "manifest.json",
            resources_root / "extension" / ".output" / "chrome-mv3" / "manifest.json",
            resources_root / "extension" / ".output" / "chrome-mv3-dev" / "manifest.json",
        ]

    def trusted_extension_id(self) -> str | None:
        # Chrome 的扩展 ID 是公钥 SHA-256 前 16 字节按半字节映射到 a-p；公钥本身不是认证凭据。
        for manifest_path in self._manifest_paths:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError, ValueError):
                return None
            key = manifest.get("key") if isinstance(manifest, dict) else None
            if not isinstance(key, str):
                return None
            try:
                public_key = base64.b64decode(key, validate=True)
            except (ValueError, binascii.Error):
                return None
            digest = hashlib.sha256(public_key).digest()[:16]
            return "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 15))
        return None

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
