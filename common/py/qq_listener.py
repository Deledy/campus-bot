"""QQ 群 @ 事件监听：gateway 连接、鉴权、心跳、断线重连与消息去重。

业务逻辑由调用方传入的 async 回调决定：

    listener = GroupListener("menu", on_at)
    asyncio.run(listener.run_forever())

on_at(d) 收到的 d 是 GROUP_AT_MESSAGE_CREATE 的原始事件体，
调用方从中取 group_openid / id / content，再决定回复什么。
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Awaitable, Callable

import requests

import bots
from common import log

API = "https://api.sgroup.qq.com"
# 群聊/单聊事件（1<<25）就够收到 GROUP_AT_MESSAGE_CREATE
INTENTS = 1 << 25

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10

AtHandler = Callable[[dict], Awaitable[None]]


class GroupListener:
    """一个机器人一条长连接，收到 @ 就交给 on_at 处理。"""

    def __init__(self, bot_name: str, on_at: AtHandler) -> None:
        self.bot = bots.get_bot(bot_name)
        self.on_at = on_at
        # 断线重连后平台可能重发同一条事件，按消息 id 去重
        self._seen: deque[str] = deque(maxlen=200)

    async def run_forever(self) -> None:
        backoff = 3
        while True:
            try:
                await self._connect_once()
                backoff = 3
                log("连接已断开，准备重连")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log(f"连接异常：{exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _connect_once(self) -> None:
        # 延迟导入：本地只想 --preview 时不必装 websockets
        import websockets

        # 重连时强制刷新 token，避免拿着过期 token 一直失败
        token = self.bot.token(force=True)
        gateway = self._gateway(token)
        log(f"连接 gateway：{gateway}")

        async with websockets.connect(gateway, max_size=2**23) as ws:
            hello = json.loads(await ws.recv())
            interval = hello.get("d", {}).get("heartbeat_interval", 30000)
            await ws.send(json.dumps({
                "op": OP_IDENTIFY,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": INTENTS,
                    "shard": [0, 1],
                    "properties": {},
                },
            }))
            log(f"已鉴权（心跳 {interval}ms），等待群里 @ …")

            seq: list[int | None] = [None]
            hb = asyncio.create_task(self._heartbeat(ws, interval, seq))
            try:
                while True:
                    event = json.loads(await ws.recv())
                    op = event.get("op")
                    if event.get("s") is not None:
                        seq[0] = event["s"]

                    if op == OP_RECONNECT:
                        log("服务端要求重连")
                        return
                    if op == OP_INVALID_SESSION:
                        log("会话失效（检查机器人是否开通了群聊消息权限）")
                        return
                    if op != OP_DISPATCH:
                        continue

                    t = event.get("t")
                    d = event.get("d") or {}
                    if t == "READY":
                        user = d.get("user") or {}
                        log(f"已就绪：{user.get('username') or user.get('id')}")
                    elif t == "GROUP_AT_MESSAGE_CREATE":
                        await self._dispatch(d)
            finally:
                hb.cancel()

    async def _heartbeat(self, ws, interval_ms: int, seq: list[int | None]) -> None:
        while True:
            await asyncio.sleep(interval_ms / 1000)
            try:
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": seq[0]}))
            except Exception:  # noqa: BLE001
                return

    async def _dispatch(self, d: dict) -> None:
        group = str(d.get("group_openid") or "")
        msg_id = str(d.get("id") or "")
        if not group or not msg_id:
            return
        if msg_id in self._seen:
            return
        self._seen.append(msg_id)

        content = str(d.get("content") or "").strip()
        author = str((d.get("author") or {}).get("member_openid") or "?")
        log(f"收到 @：{author} 在群 {group[:8]}…，内容：{content[:40] or '（空）'}")
        await self.on_at(d)

    @staticmethod
    def _gateway(token: str) -> str:
        resp = requests.get(
            f"{API}/gateway", headers={"Authorization": f"QQBot {token}"}, timeout=15
        )
        resp.raise_for_status()
        url = (resp.json() or {}).get("url")
        if not url:
            raise RuntimeError(f"获取 gateway 失败：{resp.text[:200]}")
        return str(url)
