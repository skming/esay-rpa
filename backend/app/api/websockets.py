"""WebSocket routes for picker, extension bridge, and task log streaming."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

router = APIRouter()


def _state_service(websocket: WebSocket, name: str) -> Any:
    """从 app.state 取主应用装配好的单例服务，避免 router 模块重复初始化运行时状态。"""
    return getattr(websocket.app.state, name)


@router.websocket("/ws/picker")
async def picker_socket(websocket: WebSocket) -> None:
    picker_service = _state_service(websocket, "picker_service")
    await websocket.accept()
    try:
        result = await picker_service.wait_for_result()
        if result is None:
            await websocket.send_json({"type": "cancel"})
        else:
            await websocket.send_json({"type": "capture", **result})
    except (asyncio.CancelledError, WebSocketDisconnect):
        pass
    finally:
        await websocket.close()


@router.websocket("/ws/extension/bridge")
async def extension_bridge_socket(websocket: WebSocket) -> None:
    """浏览器扩展后台脚本的长连接入口，和 Playwright 执行器共享后端调度层。"""
    extension_bridge_service = _state_service(websocket, "extension_bridge_service")
    await extension_bridge_service.handle_connection(websocket)


@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs_socket(websocket: WebSocket, task_id: str) -> None:
    broker = _state_service(websocket, "log_broker")
    task_manager = _state_service(websocket, "task_manager")

    # 先 accept 再发错误，避免浏览器收到非标准 close code 产生控制台报错。
    await websocket.accept()
    if await task_manager.get_task(task_id) is None:
        await websocket.send_json({"type": "error", "message": f"Task not found: {task_id}"})
        await websocket.close(code=1008, reason="Task not found")
        return

    queue = broker.subscribe(task_id)
    try:
        for log in await task_manager.get_logs(task_id) or []:
            await websocket.send_json(jsonable_encoder(log, by_alias=True))
        while True:
            event = await queue.get()
            await websocket.send_json(jsonable_encoder(event, by_alias=True))
    except (asyncio.CancelledError, WebSocketDisconnect):
        return
    finally:
        broker.unsubscribe(task_id, queue)
