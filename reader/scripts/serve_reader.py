"""常驻服务：群里 @ 操前小读机器人 → 现场生成 docx → 回传到群里。

    python reader/scripts/serve_reader.py --bot reader

为什么是常驻：
QQ 的「@ 机器人」是一条 WebSocket 事件，不是定时任务。进程必须一直在线，
才能收到 GROUP_AT_MESSAGE_CREATE，然后用 msg_id 做被动回复（不占主动消息额度）。

部署由 common/deploy/server/install.sh 自动生成 campus-bot-reader.service。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import deque
from pathlib import Path

import requests
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
import bots  # noqa: E402
import run_reader  # noqa: E402
from common import load_env, log  # noqa: E402
from send_qq import send_group_file, send_group_text  # noqa: E402

API = "https://api.sgroup.qq.com"
# 群聊/单聊事件（1<<25）就够收到 GROUP_AT_MESSAGE_CREATE
INTENTS = 1 << 25

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10

# @ 后面认这些词。册名必须排在年级前面匹配（「选择性必修一」里含「必修一」）
BOOK_ALIASES = {
    "选择性必修一": "选择性必修一", "选择性必修1": "选择性必修一",
    "选择性必修二": "选择性必修二", "选择性必修2": "选择性必修二",
    "选择性必修三": "选择性必修三", "选择性必修3": "选择性必修三",
    "选必一": "选择性必修一", "选必1": "选择性必修一",
    "选必二": "选择性必修二", "选必2": "选择性必修二",
    "选必三": "选择性必修三", "选必3": "选择性必修三",
    "必修一": "必修一", "必修1": "必修一",
    "必修二": "必修二", "必修2": "必修二",
    "高三复习": "高三复习",
}
GRADE_ALIASES = {"高一": "高一", "高二": "高二", "高三": "高三"}
RANDOM_WORDS = ("随机", "任意", "全部", "随便")

HELP_TEXT = (
    "用法：@我 + 年级或册名\n"
    "· 年级：高一 / 高二 / 高三\n"
    "· 册名：必修一、必修二、选择性必修一、选择性必修二、选择性必修三、高三复习\n"
    "· 随机：不指定年级，全库轮转\n"
    "例：@操前小读机器人 高二"
)


def parse_request(text: str) -> tuple[str | None, str | None] | None:
    """把 @ 后面的文字解析成 (book, grade)。

    返回 None 表示没听懂，需要回用法提示；(None, None) 表示随机出刊。
    """
    raw = re.sub(r"\s+", "", text or "")
    if not raw:
        return None
    for alias in sorted(BOOK_ALIASES, key=len, reverse=True):
        if alias in raw:
            return BOOK_ALIASES[alias], None
    for alias, grade in GRADE_ALIASES.items():
        if alias in raw:
            return None, grade
    if any(word in raw for word in RANDOM_WORDS):
        return None, None
    return None


class ReaderService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.bot = bots.get_bot(name)
        self._lock = asyncio.Lock()
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
                        await self._on_at(d)
            finally:
                hb.cancel()

    async def _heartbeat(
        self, ws, interval_ms: int, seq: list[int | None]
    ) -> None:
        while True:
            await asyncio.sleep(interval_ms / 1000)
            try:
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": seq[0]}))
            except Exception:  # noqa: BLE001
                return

    async def _on_at(self, d: dict) -> None:
        group = str(d.get("group_openid") or "")
        msg_id = str(d.get("id") or "")
        content = str(d.get("content") or "").strip()
        author = str((d.get("author") or {}).get("member_openid") or "?")

        if not group or not msg_id:
            return
        if msg_id in self._seen:
            return
        self._seen.append(msg_id)

        log(f"收到 @：{author} 在群 {group[:8]}…，内容：{content[:40] or '（空）'}")

        request = parse_request(content)
        if request is None:
            await asyncio.to_thread(
                send_group_text, group, HELP_TEXT, self.name, 15, msg_id, 1,
            )
            return
        book, grade = request
        label = book or grade or "随机"
        log(f"出刊范围：{label}")

        if self._lock.locked():
            await asyncio.to_thread(
                send_group_text, group, "上一份还在生成，稍等一下…",
                self.name, 15, msg_id, 1,
            )
            return

        async with self._lock:
            try:
                target, picked = await asyncio.to_thread(
                    run_reader.build_issue, book=book, grade=grade
                )
            except Exception as exc:  # noqa: BLE001
                log(f"出刊失败：{exc}")
                await asyncio.to_thread(
                    send_group_text, group, f"出刊失败：{exc}"[:300],
                    self.name, 15, msg_id, 1,
                )
                return

            titles = "、".join(t["title"] for t in picked)
            caption = f"📖 操前小读 · {label}：{titles}"
            try:
                await asyncio.to_thread(
                    send_group_file, group, target, self.name, msg_id, 1, caption
                )
                log(f"已回传 {target.name}（{titles}）")
            except Exception as exc:  # noqa: BLE001
                log(f"文件发送失败：{exc}")
                await asyncio.to_thread(
                    send_group_text, group, f"文件发送失败：{exc}"[:300],
                    self.name, 15, msg_id, 2,
                )

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


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="常驻监听群 @，生成并回传操前小读 docx")
    ap.add_argument("--bot", default="reader", choices=bots.list_bots())
    args = ap.parse_args()

    if bots.bot_handler(args.bot) != "reader":
        log(f"机器人「{args.bot}」的 handler 不是 reader")
        return 1

    service = ReaderService(args.bot)
    log(f"启动常驻监听：{service.bot.label}（{args.bot}）")
    try:
        asyncio.run(service.run_forever())
    except KeyboardInterrupt:
        log("已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
