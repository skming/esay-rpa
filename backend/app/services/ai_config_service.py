from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core import storage

_CONFIG_FILENAME = "config.json"
_CATALOG_FILENAME = "model_catalog.json"

# backend/app/services/ → backend/app/ → backend/ → backend/config/
_CATALOG_PATH = Path(__file__).parent.parent.parent / "config" / _CATALOG_FILENAME


def _mask_key(value: str) -> str:
    """Return a masked representation: first-4 + **** + last-4, or **** for short keys."""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def _load_catalog() -> list[dict[str, Any]]:
    """Load model catalog from config/model_catalog.json."""
    path = _CATALOG_PATH
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    # Fallback: return empty list so the app still starts
    return []


AI_MODEL_CATALOG: list[dict[str, Any]] = _load_catalog()

DEFAULT_MODEL = "claude-sonnet-4-6"

# All env keys that can be configured
_ALL_ENV_KEYS = sorted({m["env_key"] for m in AI_MODEL_CATALOG if "env_key" in m})


class AiConfigService:
    """Persists AI model API keys and default model selection to a JSON file."""

    def __init__(self, app_data_dir: str | None = None) -> None:
        self._path = storage.resolve_ai_config_path() if app_data_dir is None else Path(app_data_dir) / "ai" / _CONFIG_FILENAME

    def load(self) -> dict[str, Any]:
        """Return the stored config, merged with any env-var overrides."""
        stored = self._read_file()
        return stored

    def _read_file(self) -> dict[str, Any]:
        _default: dict[str, Any] = {"default_model": DEFAULT_MODEL, "api_keys": {}, "base_urls": {}}
        if not self._path.exists():
            return _default.copy()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return _default.copy()
            data.setdefault("default_model", DEFAULT_MODEL)
            data.setdefault("api_keys", {})
            data.setdefault("base_urls", {})
            return data
        except Exception:
            return _default.copy()

    def save(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge patch into existing config and persist. Returns new config."""
        current = self._read_file()

        if "default_model" in patch and isinstance(patch["default_model"], str):
            current["default_model"] = patch["default_model"]

        if "api_keys" in patch and isinstance(patch["api_keys"], dict):
            for key, value in patch["api_keys"].items():
                if key in _ALL_ENV_KEYS:
                    if isinstance(value, str) and value.strip():
                        stripped = value.strip()
                        # Ignore masked values echoed back by the frontend to avoid
                        # overwriting the real key with the display placeholder.
                        if "****" not in stripped:
                            current["api_keys"][key] = stripped
                    elif value == "" or value is None:
                        current["api_keys"].pop(key, None)

        if "base_urls" in patch and isinstance(patch["base_urls"], dict):
            for key, value in patch["base_urls"].items():
                if key in _ALL_ENV_KEYS:
                    if isinstance(value, str) and value.strip():
                        current.setdefault("base_urls", {})[key] = value.strip()
                    elif value == "" or value is None:
                        current.get("base_urls", {}).pop(key, None)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return current

    def apply_to_env(self, config: dict[str, Any]) -> None:
        """Inject stored API keys into os.environ so LiteLLM picks them up."""
        for key, value in config.get("api_keys", {}).items():
            if isinstance(value, str) and value.strip():
                os.environ.setdefault(key, value.strip())

    def get_masked_config(self) -> dict[str, Any]:
        """Return config with API keys masked for display (first/last 4 chars only)."""
        config = self._read_file()
        masked_keys: dict[str, str] = {}
        for key in _ALL_ENV_KEYS:
            stored = config["api_keys"].get(key, "")
            env_val = os.environ.get(key, "")
            raw = stored or env_val
            masked_keys[key] = _mask_key(raw)
        return {
            "default_model": config["default_model"],
            "api_keys": masked_keys,
            "base_urls": config.get("base_urls", {}),
        }

    def _find_env_key(self, model_id: str) -> str:
        """Return the env_key for model_id from the catalog."""
        for m in AI_MODEL_CATALOG:
            if m["id"] == model_id:
                return m.get("env_key", "")
        return ""

    def get_api_key_for_model(self, model_id: str) -> str | None:
        """Return the stored API key for the provider of the given model, or None."""
        config = self._read_file()
        api_keys: dict[str, str] = config.get("api_keys", {})
        env_vars = __import__("os").environ
        env_key = self._find_env_key(model_id)
        if not env_key:
            return None
        stored = api_keys.get(env_key, "").strip()
        return stored or env_vars.get(env_key, "") or None

    def get_base_url_for_model(self, model_id: str) -> str | None:
        """Return the custom base_url override for the provider of the given model, or None."""
        config = self._read_file()
        base_urls: dict[str, str] = config.get("base_urls", {})
        if not base_urls:
            return None
        env_key = self._find_env_key(model_id)
        if not env_key:
            return None
        return base_urls.get(env_key, "").strip() or None
