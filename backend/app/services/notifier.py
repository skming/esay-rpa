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
    """从不抛出异常——通知渠道配置错误或不可达不应影响流程执行。"""

    def __init__(self, config_service: NotificationConfigService | None = None) -> None:
        self._config_service = config_service or NotificationConfigService()

    async def notify_human_takeover(self, *, flow_name: str, node_title: str, message: str, task_id: str) -> None:
        title = f"「{flow_name}」需要人工接管"
        body = message.split("\n⏱")[0]  # 去掉内部编码的超时毫秒数
        text = f"#### {title}\n- 节点：{node_title}\n- 说明：{body}\n- 任务 ID：{task_id}"
        await self.send(text)

    async def send(self, markdown_text: str) -> None:
        config = self._config_service.load()
        if not config.get("dingtalk_enabled"):
            return
        webhook_url = str(config.get("dingtalk_webhook_url") or "").strip()
        if not webhook_url:
            return
        secret = str(config.get("dingtalk_secret") or "").strip()

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
                if result.get("errcode") not in (0, None):
                    logger.warning("DingTalk notification rejected: %s", result)
        except Exception:
            logger.exception("Failed to send DingTalk notification")
