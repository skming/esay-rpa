from __future__ import annotations

import os

import pytest

from app.services.ai_config_service import AI_MODEL_CATALOG, AiConfigService

_ENV_KEY = "ANTHROPIC_API_KEY"


@pytest.fixture(autouse=True)
def _restore_module_catalog():
    """还原模块级全局。AI_MODEL_CATALOG 被 _refresh_cache 原地改写、_ALL_ENV_KEYS 被整体重赋值，
    本文件的测试会删模型，不还原就顺着同一个进程漏给后面的测试——test_api 的目录断言
    依赖 claude-sonnet-5 还在目录里，把 anthropic 删空会让它报「模型不在目录中」。
    """
    import app.services.ai_config_service as mod

    catalog = [dict(m) for m in mod.AI_MODEL_CATALOG]
    env_keys = set(mod._ALL_ENV_KEYS)
    yield
    mod.AI_MODEL_CATALOG.clear()
    mod.AI_MODEL_CATALOG.extend(catalog)
    mod._ALL_ENV_KEYS = env_keys


@pytest.fixture()
def service(tmp_path, monkeypatch: pytest.MonkeyPatch) -> AiConfigService:
    monkeypatch.delenv(_ENV_KEY, raising=False)
    return AiConfigService(app_data_dir=str(tmp_path))


def test_save_empty_string_clears_api_key(service: AiConfigService) -> None:
    service.save({"api_keys": {_ENV_KEY: "sk-ant-test-123"}})
    assert service.load()["api_keys"] == {_ENV_KEY: "sk-ant-test-123"}

    service.save({"api_keys": {_ENV_KEY: ""}})
    assert service.load()["api_keys"] == {}


def test_apply_to_env_removes_cleared_injected_key(service: AiConfigService) -> None:
    service.save({"api_keys": {_ENV_KEY: "sk-ant-test-123"}})
    service.apply_to_env(service.load())
    assert os.environ[_ENV_KEY] == "sk-ant-test-123"

    service.save({"api_keys": {_ENV_KEY: ""}})
    service.apply_to_env(service.load())
    assert _ENV_KEY not in os.environ
    assert service.get_masked_config()["api_keys"][_ENV_KEY] == ""


def test_apply_to_env_updates_injected_key(service: AiConfigService) -> None:
    service.save({"api_keys": {_ENV_KEY: "sk-ant-old-value"}})
    service.apply_to_env(service.load())

    service.save({"api_keys": {_ENV_KEY: "sk-ant-new-value"}})
    service.apply_to_env(service.load())
    assert os.environ[_ENV_KEY] == "sk-ant-new-value"

    service.save({"api_keys": {_ENV_KEY: ""}})
    service.apply_to_env(service.load())


def test_apply_to_env_never_touches_shell_env(
    service: AiConfigService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_ENV_KEY, "from-user-shell")

    service.save({"api_keys": {_ENV_KEY: "sk-ant-test-123"}})
    service.apply_to_env(service.load())
    assert os.environ[_ENV_KEY] == "from-user-shell"

    service.save({"api_keys": {_ENV_KEY: ""}})
    service.apply_to_env(service.load())
    assert os.environ[_ENV_KEY] == "from-user-shell"


def test_save_ignores_masked_placeholder_value(service: AiConfigService) -> None:
    service.save({"api_keys": {_ENV_KEY: "sk-ant-test-123"}})
    service.save({"api_keys": {_ENV_KEY: "sk-a****-123"}})
    assert service.load()["api_keys"] == {_ENV_KEY: "sk-ant-test-123"}


class _MemoryCatalogStore:
    """内存版 ModelCatalogStore，避免 init_catalog 的测试碰真实数据库。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: list[dict] = list(rows or [])

    async def create_schema(self) -> None:
        return None

    async def is_empty(self) -> bool:
        return not self.rows

    async def list(self) -> list[dict]:
        return [dict(r) for r in self.rows]

    async def replace_all(self, catalog: list[dict]) -> None:
        self.rows = [dict(c) for c in catalog]

    async def close(self) -> None:
        return None


def _service_with(tmp_path, store: _MemoryCatalogStore) -> AiConfigService:
    return AiConfigService(app_data_dir=str(tmp_path), catalog_store=store)


@pytest.mark.asyncio
async def test_new_seed_models_reach_existing_installs(tmp_path) -> None:
    """老版本只在空库时播种，升级后新增的机型永远进不了已有安装。"""
    existing = dict(AI_MODEL_CATALOG[0])
    store = _MemoryCatalogStore([
        {k: existing[k] for k in ("id", "provider", "env_key") if k in existing}
    ])
    service = _service_with(tmp_path, store)
    await service.init_catalog()

    ids = [r["id"] for r in store.rows]
    assert existing["id"] in ids, "已有机型不该在刷新中丢失"
    # 清单里除它以外的机型都该补进来——断言某个具体厂商只会让改版时误报
    assert set(ids) >= {m["id"] for m in AI_MODEL_CATALOG}


@pytest.mark.asyncio
async def test_deleted_models_are_not_resurrected_on_restart(tmp_path) -> None:
    """播种过的 ID 记在配置里，用户删掉后下次启动不该再冒出来。

    删哪个机型不影响这条行为，所以从实际播种结果里取一个——写死某个 ID，
    机型清单一改版测试就红，红的却不是它要守的东西。
    """
    store = _MemoryCatalogStore()
    service = _service_with(tmp_path, store)
    await service.init_catalog()
    seeded = [r["id"] for r in store.rows]
    assert seeded, "内置机型清单为空，播种逻辑本身有问题"
    victim = seeded[0]

    await service.delete_catalog_model(victim)
    await service.init_catalog()
    assert not any(r["id"] == victim for r in store.rows)
    # 只有被删的那个不该回来，其余机型照常播种
    assert len(store.rows) == len(seeded) - 1


@pytest.mark.asyncio
async def test_untouched_seed_models_follow_the_shipped_catalog(tmp_path) -> None:
    """recommended/badge/窗口这些随版本修订的字段，不刷新就只对全新安装生效。"""
    shipped = dict(AI_MODEL_CATALOG[0])
    store = _MemoryCatalogStore([
        {"id": shipped["id"], "label": "旧标签", "provider": shipped.get("provider", "openai"),
         "env_key": shipped.get("env_key", "OPENAI_API_KEY"), "context_window": 1, "tier": "weak"}
    ])
    await _service_with(tmp_path, store).init_catalog()

    row = next(r for r in store.rows if r["id"] == shipped["id"])
    # 断言「内置清单里的每个字段都被刷回去」，而不是某个具体标签/窗口大小——后者随机型改版而变，
    # 逐字段比对还能覆盖 recommended/badge 这类以后新增的字段，不用回来改测试
    assert {k: row.get(k) for k in shipped} == shipped


@pytest.mark.asyncio
async def test_user_edits_survive_the_seed_refresh(tmp_path) -> None:
    """用户改过的机型不能被启动刷新悄悄覆盖回去。"""
    store = _MemoryCatalogStore()
    service = _service_with(tmp_path, store)
    await service.init_catalog()

    target = AI_MODEL_CATALOG[0]["id"]
    await service.update_catalog_model(target, {"label": "我自己的叫法"})
    await service.init_catalog()

    row = next(r for r in store.rows if r["id"] == target)
    assert row["label"] == "我自己的叫法"
    assert row["user_edited"] is True


@pytest.mark.asyncio
async def test_deleting_a_providers_last_model_keeps_the_provider(tmp_path) -> None:
    """厂商分组曾按「目录里还剩几个模型」推导，删掉某厂商最后一个模型分组就整条消失：
    设置页没有它的 API Key 输入框，也没有「添加模型」入口——而添加模型必须挂在已有分组下，
    于是这个厂商再也加不回来，已存的密钥变成读不到又清不掉的孤儿。
    删哪个厂商不影响这条行为，所以从播种结果里挑一个，写死厂商名会在改版时误报。
    """
    store = _MemoryCatalogStore()
    service = _service_with(tmp_path, store)
    await service.init_catalog()

    victim = service.get_provider_groups()[0]
    doomed = [m["id"] for m in service.get_model_catalog() if m.get("provider") == victim["id"]]
    assert doomed, "挑中的厂商本来就没有模型，测不到删空这件事"
    for model_id in doomed:
        await service.delete_catalog_model(model_id)

    assert not any(m.get("provider") == victim["id"] for m in service.get_model_catalog())
    groups = service.get_provider_groups()
    assert victim["id"] in [g["id"] for g in groups], "删空最后一个模型后厂商分组丢了"
    # label/env_key 也要还在，否则输入框存在却对不上密钥
    restored = next(g for g in groups if g["id"] == victim["id"])
    assert restored["env_key"] == victim["env_key"]
    assert restored["label"] == victim["label"]


@pytest.mark.asyncio
async def test_an_emptied_providers_key_can_still_be_read_and_cleared(tmp_path) -> None:
    """_ALL_ENV_KEYS 是 api_keys 能否写入的闸门。跟着目录缩水的话，
    某厂商被删空后它已存的密钥既改不动也删不掉，只能留在磁盘上。"""
    store = _MemoryCatalogStore()
    service = _service_with(tmp_path, store)
    await service.init_catalog()

    victim = service.get_provider_groups()[0]
    env_key = victim["env_key"]
    service.save({"api_keys": {env_key: "sk-still-mine"}})

    for model_id in [m["id"] for m in service.get_model_catalog() if m.get("provider") == victim["id"]]:
        await service.delete_catalog_model(model_id)

    assert service.load()["api_keys"][env_key] == "sk-still-mine", "密钥不该因为模型被删空而消失"
    # 改得动
    service.save({"api_keys": {env_key: "sk-rotated"}})
    assert service.load()["api_keys"][env_key] == "sk-rotated"
    # 也清得掉
    service.save({"api_keys": {env_key: ""}})
    assert env_key not in service.load()["api_keys"]
