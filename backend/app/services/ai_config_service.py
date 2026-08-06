from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core import storage
from app.services.model_catalog_store import ModelCatalogStore, SqlAlchemyModelCatalogStore
from app.services.schedule_store import create_schedule_engine

_CONFIG_FILENAME = "config.json"
_CATALOG_FILENAME = "model_catalog.json"

# 仅用于数据库为空时的首次播种，之后所有读写都走数据库，不再写回此文件
_SEED_CATALOG_PATH = Path(__file__).parent.parent.parent / "config" / _CATALOG_FILENAME


def _mask_key(value: str) -> str:
    """Return a masked representation: first-4 + **** + last-4, or **** for short keys."""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def _load_seed_catalog() -> list[dict[str, Any]]:
    """Load the bundled default catalog (used only to seed an empty database)."""
    path = _SEED_CATALOG_PATH
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


# 进程内缓存，供同步调用方读取；init_catalog() 在启动时从数据库填充，写操作后立即刷新。
AI_MODEL_CATALOG: list[dict[str, Any]] = _load_seed_catalog()

DEFAULT_MODEL = "claude-sonnet-5"


def _provider_groups_from(catalog: list[dict[str, Any]]) -> list[dict[str, str]]:
    """按目录出现顺序取厂商，每个厂商只留第一次见到的 label/env_key。"""
    groups: dict[str, dict[str, str]] = {}
    for model in catalog:
        provider = str(model.get("provider", "")).strip()
        env_key = str(model.get("env_key", "")).strip()
        if not provider or not env_key or provider in groups:
            continue
        groups[provider] = {
            "id": provider,
            "label": str(model.get("provider_label", "")).strip() or provider,
            "env_key": env_key,
        }
    return list(groups.values())


# 厂商清单以播种目录为底：厂商不随「这个厂商此刻还剩几个模型」存亡。删掉某厂商最后一个模型后，
# 若厂商跟着消失，设置页就没有它的 API Key 输入框和「添加模型」入口了——而添加模型必须挂在
# 已有厂商下，于是这个厂商再也加不回来，已存的密钥也变成既读不到又删不掉的孤儿。
_SEED_PROVIDER_GROUPS: list[dict[str, str]] = _provider_groups_from(_load_seed_catalog())
_SEED_ENV_KEYS: set[str] = {g["env_key"] for g in _SEED_PROVIDER_GROUPS}

# 播种厂商的 env_key 常驻：这个集合是 api_keys/base_urls 能否写入的闸门（见 save()），
# 跟着目录缩水会让孤儿密钥连清空都做不到，还会在下次保存时静默丢掉 provider_models。
_ALL_ENV_KEYS: set[str] = _SEED_ENV_KEYS | {m["env_key"] for m in AI_MODEL_CATALOG if "env_key" in m}


def _normalize_catalog_model(raw: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化模型目录项，避免写入无法被运行时识别的数据。"""
    model_id = str(raw.get("id", "")).strip()
    label = str(raw.get("label", "") or model_id).strip()
    provider = str(raw.get("provider", "")).strip()
    env_key = str(raw.get("env_key", "")).strip()
    if not model_id:
        raise ValueError("模型 ID 不能为空")
    if not provider:
        raise ValueError("服务商不能为空")
    if not env_key:
        raise ValueError("环境变量 Key 不能为空")

    context_window_raw = raw.get("context_window", 0)
    try:
        context_window = int(context_window_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("上下文长度必须是数字") from exc
    if context_window < 0:
        raise ValueError("上下文长度不能小于 0")

    normalized: dict[str, Any] = {
        "id": model_id,
        "label": label,
        "provider": provider,
        "env_key": env_key,
        "context_window": context_window,
    }

    tier = str(raw.get("tier", "")).strip()
    if tier:
        normalized["tier"] = tier
    if bool(raw.get("recommended", False)):
        normalized["recommended"] = True
    if bool(raw.get("no_vision", False)):
        normalized["no_vision"] = True
    if bool(raw.get("local", False)):
        normalized["local"] = True
    # 已被同厂商新版取代：仍可选用，但在选择器里排到分组末尾并标注
    if bool(raw.get("legacy", False)):
        normalized["legacy"] = True
    if bool(raw.get("user_edited", False)):
        normalized["user_edited"] = True
    provider_label = str(raw.get("provider_label", "")).strip()
    if provider_label:
        normalized["provider_label"] = provider_label
    badge = str(raw.get("badge", "")).strip()
    if badge:
        normalized["badge"] = badge
    return normalized


class AiConfigService:
    """Persists AI model API keys and default model selection to a JSON file,
    and the editable model catalog to the application database."""

    def __init__(
        self,
        app_data_dir: str | None = None,
        catalog_store: ModelCatalogStore | None = None,
        database_url: str | None = None,
    ) -> None:
        self._path = storage.resolve_ai_config_path() if app_data_dir is None else Path(app_data_dir) / "ai" / _CONFIG_FILENAME
        # 记录由本服务注入 os.environ 的密钥，区别于用户 shell 里真实存在的环境变量：
        # 只有我们注入的才允许在清除/更新配置时被覆盖或移除。
        self._injected_env_keys: set[str] = set()
        resolved_database_url = database_url or os.getenv("DATABASE_URL") or f"sqlite+aiosqlite:///{storage.resolve_database_path()}"
        self._catalog_store: ModelCatalogStore = catalog_store or SqlAlchemyModelCatalogStore(create_schedule_engine(resolved_database_url))

    async def init_catalog(self) -> None:
        """建表并加载进程内缓存；空库时用内置默认目录播种，之后只补新增机型。"""
        await self._catalog_store.create_schema()
        seed = _load_seed_catalog()

        if await self._catalog_store.is_empty():
            await self._catalog_store.replace_all(seed)
            self._remember_seeded_ids([m["id"] for m in seed])
            await self._refresh_cache(seed)
            return

        catalog = await self._catalog_store.list()
        # 老版本只在空库时播种，升级后新增的机型永远进不了已有安装
        seeded = set(self._read_file().get("seeded_model_ids", []))
        seed_by_id = {m["id"]: m for m in seed}
        existing = {item.get("id") for item in catalog}
        # 播种过又不在目录里 = 用户手动删过，不再塞回去
        additions = [m for m in seed if m["id"] not in existing and m["id"] not in seeded]

        # 用户没动过的内置机型跟随种子刷新，否则 legacy/recommended/窗口这些
        # 随版本修订的字段只对全新安装生效，已有安装永远停在初次播种的那一版
        refreshed = False
        for index, item in enumerate(catalog):
            fresh = seed_by_id.get(str(item.get("id")))
            if fresh is not None and not item.get("user_edited") and item != fresh:
                catalog[index] = dict(fresh)
                refreshed = True

        if additions or refreshed:
            catalog.extend(additions)
            await self._catalog_store.replace_all(catalog)
        if additions or not seeded:
            self._remember_seeded_ids([m["id"] for m in seed])
        await self._refresh_cache(catalog)

    def _remember_seeded_ids(self, model_ids: list[str]) -> None:
        config = self._read_file()
        config["seeded_model_ids"] = sorted(set(config.get("seeded_model_ids", [])) | set(model_ids))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    async def close_catalog(self) -> None:
        await self._catalog_store.close()

    async def _refresh_cache(self, catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        global _ALL_ENV_KEYS
        AI_MODEL_CATALOG.clear()
        AI_MODEL_CATALOG.extend(catalog)
        # 并上播种 key：目录里某厂商被删空时，它的密钥仍要能读能改能清
        _ALL_ENV_KEYS = _SEED_ENV_KEYS | {m["env_key"] for m in AI_MODEL_CATALOG if "env_key" in m}
        return AI_MODEL_CATALOG

    def get_provider_groups(self) -> list[dict[str, str]]:
        """设置页的厂商分组：播种厂商恒在，用户新增厂商追加在后。

        分组不由「当前目录里还有没有这个厂商的模型」决定，否则删掉最后一个模型
        就等于把这个厂商的密钥入口一起删了，且无法恢复。
        """
        groups = {g["id"]: dict(g) for g in _SEED_PROVIDER_GROUPS}
        for group in _provider_groups_from(self.get_model_catalog()):
            # 播种厂商以播种的 label/env_key 为准，避免用户改一行模型就换掉整组身份
            groups.setdefault(group["id"], group)
        return list(groups.values())

    async def _persist_catalog(self, catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        await self._catalog_store.replace_all(catalog)
        return await self._refresh_cache(catalog)

    def load(self) -> dict[str, Any]:
        stored = self._read_file()
        return stored

    def get_model_catalog(self) -> list[dict[str, Any]]:
        return [dict(item) for item in AI_MODEL_CATALOG]

    async def add_catalog_model(self, model: dict[str, Any]) -> list[dict[str, Any]]:
        normalized = _normalize_catalog_model(model)
        catalog = self.get_model_catalog()
        if any(item.get("id") == normalized["id"] for item in catalog):
            raise ValueError(f"模型已存在: {normalized['id']}")
        catalog.append(normalized)
        return await self._persist_catalog(catalog)

    async def update_catalog_model(self, model_id: str, patch: dict[str, Any]) -> list[dict[str, Any]]:
        target = model_id.strip()
        if not target:
            raise ValueError("模型 ID 不能为空")
        catalog = self.get_model_catalog()
        index = next((i for i, item in enumerate(catalog) if item.get("id") == target), None)
        if index is None:
            raise ValueError(f"模型不存在: {target}")
        # 打上标记，启动时的种子刷新就不会覆盖掉用户的改动
        merged = {**catalog[index], **patch, "id": target, "user_edited": True}
        catalog[index] = _normalize_catalog_model(merged)
        return await self._persist_catalog(catalog)

    async def delete_catalog_model(self, model_id: str) -> list[dict[str, Any]]:
        target = model_id.strip()
        if not target:
            raise ValueError("模型 ID 不能为空")
        catalog = self.get_model_catalog()
        next_catalog = [item for item in catalog if item.get("id") != target]
        if len(next_catalog) == len(catalog):
            raise ValueError(f"模型不存在: {target}")
        if self._read_file().get("default_model") == target and next_catalog:
            config = self._read_file()
            config["default_model"] = next_catalog[0]["id"]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return await self._persist_catalog(next_catalog)

    def _read_file(self) -> dict[str, Any]:
        _default: dict[str, Any] = {
            "default_model": DEFAULT_MODEL,
            "api_keys": {},
            "base_urls": {},
            "provider_models": {},
            "custom_models": [],
            "seeded_model_ids": [],
        }
        if not self._path.exists():
            return _default.copy()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return _default.copy()
            data.setdefault("default_model", DEFAULT_MODEL)
            data.setdefault("api_keys", {})
            data.setdefault("base_urls", {})
            data.setdefault("provider_models", {})
            data.setdefault("custom_models", [])
            data.setdefault("seeded_model_ids", [])
            return data
        except Exception:
            return _default.copy()

    def save(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self._read_file()

        if "default_model" in patch and isinstance(patch["default_model"], str):
            current["default_model"] = patch["default_model"]

        if "provider_models" in patch and isinstance(patch["provider_models"], dict):
            pm: dict[str, Any] = {}
            for env_key, entries in patch["provider_models"].items():
                if env_key not in _ALL_ENV_KEYS or not isinstance(entries, list):
                    continue
                valid = []
                for m in entries:
                    if not isinstance(m, dict):
                        continue
                    mid = str(m.get("id", "")).strip()
                    if not mid:
                        continue
                    valid.append({"id": mid, "label": str(m.get("label", "") or mid).strip()})
                pm[env_key] = valid
            current["provider_models"] = pm

        if "custom_models" in patch and isinstance(patch["custom_models"], list):
            validated: list[dict[str, Any]] = []
            for m in patch["custom_models"]:
                if not isinstance(m, dict):
                    continue
                model_id = str(m.get("id", "")).strip()
                if not model_id:
                    continue
                validated.append({
                    "id": model_id,
                    "label": str(m.get("label", "") or model_id).strip(),
                    "provider": str(m.get("provider", "custom")).strip() or "custom",
                    "env_key": str(m.get("env_key", "")).strip(),
                    "base_url": str(m.get("base_url", "")).strip(),
                })
            current["custom_models"] = validated

        custom_env_keys: set[str] = {m.get("env_key", "") for m in current.get("custom_models", []) if m.get("env_key")}
        allowed_keys = _ALL_ENV_KEYS | custom_env_keys

        if "api_keys" in patch and isinstance(patch["api_keys"], dict):
            for key, value in patch["api_keys"].items():
                if key in allowed_keys:
                    if isinstance(value, str) and value.strip():
                        stripped = value.strip()
                        if "****" not in stripped:
                            current["api_keys"][key] = stripped
                    elif value == "" or value is None:
                        current["api_keys"].pop(key, None)

        if "base_urls" in patch and isinstance(patch["base_urls"], dict):
            for key, value in patch["base_urls"].items():
                if key in allowed_keys:
                    if isinstance(value, str) and value.strip():
                        current.setdefault("base_urls", {})[key] = value.strip()
                    elif value == "" or value is None:
                        current.get("base_urls", {}).pop(key, None)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return current

    def apply_to_env(self, config: dict[str, Any]) -> None:
        """把存储的 API key 同步进 os.environ 供 LiteLLM 读取；用户 shell 自带的环境变量永不覆盖或删除，仅本方法注入的密钥随配置增删。"""
        stored = {
            key: value.strip()
            for key, value in config.get("api_keys", {}).items()
            if isinstance(value, str) and value.strip()
        }
        for key in self._injected_env_keys - stored.keys():
            os.environ.pop(key, None)
            self._injected_env_keys.discard(key)
        for key, value in stored.items():
            if key in self._injected_env_keys or key not in os.environ:
                os.environ[key] = value
                self._injected_env_keys.add(key)

    def get_masked_config(self) -> dict[str, Any]:
        config = self._read_file()
        custom_models: list[dict[str, Any]] = config.get("custom_models", [])
        custom_env_keys = {m.get("env_key", "") for m in custom_models if m.get("env_key")}
        all_keys = _ALL_ENV_KEYS | custom_env_keys
        masked_keys: dict[str, str] = {}
        for key in all_keys:
            stored = config["api_keys"].get(key, "")
            env_val = os.environ.get(key, "")
            raw = stored or env_val
            masked_keys[key] = _mask_key(raw)
        return {
            "default_model": config["default_model"],
            "api_keys": masked_keys,
            "base_urls": config.get("base_urls", {}),
            "provider_models": config.get("provider_models", {}),
            "custom_models": custom_models,
        }

    def get_custom_models(self) -> list[dict[str, Any]]:
        return self._read_file().get("custom_models", [])

    def _find_env_key(self, model_id: str) -> str:
        for m in AI_MODEL_CATALOG:
            if m["id"] == model_id:
                return m.get("env_key", "")
        for m in self._read_file().get("custom_models", []):
            if m.get("id") == model_id:
                return m.get("env_key", "")
        return ""

    def _find_base_url(self, model_id: str) -> str:
        for m in self._read_file().get("custom_models", []):
            if m.get("id") == model_id:
                return m.get("base_url", "")
        return ""

    def get_api_key_for_model(self, model_id: str) -> str | None:
        config = self._read_file()
        api_keys: dict[str, str] = config.get("api_keys", {})
        env_vars = __import__("os").environ
        env_key = self._find_env_key(model_id)
        if not env_key:
            return None
        stored = api_keys.get(env_key, "").strip()
        return stored or env_vars.get(env_key, "") or None

    def get_base_url_for_model(self, model_id: str) -> str | None:
        config = self._read_file()
        # 自定义模型的内联 base_url 优先级更高
        inline = self._find_base_url(model_id)
        if inline:
            return inline
        base_urls: dict[str, str] = config.get("base_urls", {})
        if not base_urls:
            return None
        env_key = self._find_env_key(model_id)
        if not env_key:
            return None
        return base_urls.get(env_key, "").strip() or None
