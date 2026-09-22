from __future__ import annotations

import httpx
import pytest

from app.services.notification_config_service import NotificationConfigService
from app.services.notifier import DingTalkNotifier


def test_enabled_notification_requires_https_webhook(tmp_path) -> None:
    service = NotificationConfigService(app_data_dir=str(tmp_path))

    with pytest.raises(ValueError, match="必须填写 Webhook"):
        service.save({"dingtalk_enabled": True})

    with pytest.raises(ValueError, match="HTTPS URL"):
        service.save({"dingtalk_webhook_url": "http://example.test/hook"})

    saved = service.save({
        "dingtalk_enabled": True,
        "dingtalk_webhook_url": "https://example.test/hook",
    })
    assert saved["dingtalk_enabled"] is True
    assert saved["dingtalk_webhook_url"] == "https://example.test/hook"


async def test_notifier_test_reports_provider_rejection(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"errcode": 310000, "errmsg": "invalid sign"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url: str, *, json: dict) -> FakeResponse:
            assert json["msgtype"] == "markdown"
            return FakeResponse()

    monkeypatch.setattr("app.services.notifier.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    with pytest.raises(RuntimeError, match="invalid sign"):
        await DingTalkNotifier().test(webhook_url="https://example.test/hook", secret="bad-secret")


async def test_notifier_network_error_does_not_expose_webhook(monkeypatch) -> None:
    secret_url = "https://example.test/hook?access_token=private-token"

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url: str, *, json: dict):
            request = httpx.Request("POST", url, json=json)
            raise httpx.ConnectError("connection failed", request=request)

    monkeypatch.setattr("app.services.notifier.httpx.AsyncClient", lambda **_kwargs: FailingClient())

    with pytest.raises(RuntimeError) as exc_info:
        await DingTalkNotifier().test(webhook_url=secret_url, secret="")
    assert "private-token" not in str(exc_info.value)
    assert str(exc_info.value) == "无法连接钉钉 Webhook"
