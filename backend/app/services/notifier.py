from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import httpx

from app.services.notification_config_service import NotificationConfigService

logger = logging.getLogger("app.notifier")

_TIMEOUT_SECONDS = 5.0  # 通知失败不应阻塞流程，控制在短超时内 best-effort 发送


def _sign(secret: str, timestamp_ms: int) -> str:
    """钉钉自定义机器人签名算法：HMAC-SHA256("{timestamp}\\n{secret}")，base64 + urlencode。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest))


class DingTalkNotifier:
    """业务通知失败只记日志；连接测试将错误返回给设置页。"""

    def __init__(self, config_service: NotificationConfigService | None = None) -> None:
        self._config_service = config_service or NotificationConfigService()

    async def send(self, markdown_text: str) -> None:
        config = self._config_service.load()
        if not config.get("dingtalk_enabled"):
            return
        webhook_url = str(config.get("dingtalk_webhook_url") or "").strip()
        if not webhook_url:
            return
        secret = str(config.get("dingtalk_secret") or "").strip()

        try:
            await self._send(webhook_url=webhook_url, secret=secret, markdown_text=markdown_text)
        except Exception as exc:
            logger.warning("Failed to send DingTalk notification: %s", exc)

    async def test(self, *, webhook_url: str, secret: str) -> None:
        await self._send(
            webhook_url=webhook_url,
            secret=secret,
            markdown_text="#### Easy RPA 测试通知\n通知渠道连接正常。",
        )

    @staticmethod
    async def _send(*, webhook_url: str, secret: str, markdown_text: str) -> None:
        url = webhook_url
        if secret:
            timestamp_ms = int(time.time() * 1000)
            sep = "&" if "?" in webhook_url else "?"
            url = f"{webhook_url}{sep}timestamp={timestamp_ms}&sign={_sign(secret, timestamp_ms)}"

        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "Easy RPA 通知", "text": markdown_text},
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException:
            raise RuntimeError("钉钉 Webhook 请求超时") from None
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"钉钉 Webhook 返回 HTTP {exc.response.status_code}") from None
        except httpx.RequestError:
            raise RuntimeError("无法连接钉钉 Webhook") from None
        except ValueError:
            raise RuntimeError("钉钉 Webhook 返回了无法解析的响应") from None
        if not isinstance(result, dict):
            raise RuntimeError("钉钉 Webhook 返回了无法解析的响应")
        if result.get("errcode") not in (0, None):
            raise RuntimeError(str(result.get("errmsg") or f"钉钉拒绝通知：{result['errcode']}"))
