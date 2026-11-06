"""抓取群 group_openid —— 每个机器人各跑一次，结果写进 .env。

为什么需要这个脚本：
QQ 机器人发群消息的接口要求 group_openid，它不是群号（群号是 123456789 这种，
openid 是一串 Base64 风格的字符）。这个值只能从「群里有人 @ 机器人」的事件里拿到，
没有别的 API 能查——所以必须有人在群里 @ 一次。
同一个群里的不同机器人，group_openid 也不同，不能共用。

用法：
    python common/scripts/capture_group_openid.py --bot menu
    # 看到「等待群消息…」后，去目标群里 @ 食谱机器人 说句话（内容随意，比如"在吗"）
    # 拿到 group_openid 后脚本会自动写入 .env 并退出
    # 默认 180 秒超时，可加 --timeout 600 延长

    python common/scripts/capture_group_openid.py --bot notice   # config.yaml 里的任意机器人键名
    python common/scripts/capture_group_openid.py --debug        # 打印所有原始事件，排错用

依赖：pip install websockets
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import requests
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import bots  # noqa: E402
from common import COMMON_DIR, load_env, log  # noqa: E402

API = "https://api.sgroup.qq.com"

# QQ 群/单聊事件（1<<25）+ 公域频道消息（1<<30）。前者就能收到 GROUP_AT_MESSAGE_CREATE。
INTENTS = (1 << 25) | (1 << 30)

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11


def get_gateway(token: str) -> str:
    resp = requests.get(
        f"{API}/gateway", headers={"Authorization": f"QQBot {token}"}, timeout=15
    )
    data = resp.json()
    url = data.get("url")
    if not url:
        sys.exit(f"获取 gateway 失败：{data}")
    return url


async def heartbeat(ws, interval_ms: int) -> None:
    """按服务端给的间隔发心跳，否则连接会被断开。"""
    seq = None
    while True:
        await asyncio.sleep(interval_ms / 1000)
        try:
            payload = {"op": OP_HEARTBEAT, "d": seq}
            await ws.send(json.dumps(payload))
        except Exception:  # noqa: BLE001
            return


async def run(bot_name: str, timeout: int, debug: bool) -> str | None:
    # 这里只需要 AppID/Secret；group_openid 正是本脚本要抓的东西，不能提前要求
    bot = bots.get_bot(bot_name, strict=False)
    token = bot.token()
    gateway = get_gateway(token)
    log(f"机器人：{bot.label}（{bot_name}）")
    log(f"Gateway: {gateway}")
    log("连接中 …")

    found: dict[str, str] = {}

    async with websockets.connect(gateway, max_size=2**23) as ws:
        hello = json.loads(await ws.recv())
        interval = hello.get("d", {}).get("heartbeat_interval", 30000)
        log(f"已连接（心跳间隔 {interval}ms），发送鉴权 …")

        await ws.send(json.dumps({
            "op": OP_IDENTIFY,
            "d": {
                "token": f"QQBot {token}",
                "intents": INTENTS,
                "shard": [0, 1],
                "properties": {},
            },
        }))

        hb = asyncio.create_task(heartbeat(ws, interval))
        log("")
        log("=" * 60)
        log("现在请到群里 @ 机器人 说一句话（随便说什么都行）")
        log(f"要 @ 的是：{bot.label}")
        log(f"最长等待 {timeout} 秒 …")
        log("=" * 60)

        try:
            async with asyncio.timeout(timeout):
                while True:
                    raw = await ws.recv()
                    event = json.loads(raw)
                    op = event.get("op")
                    t = event.get("t")

                    if debug:
                        log(f"[event] op={op} t={t}")

                    if op == OP_INVALID_SESSION:
                        log("会话失效（机器人的 intents 没权限？去开放平台确认已开通群聊消息）")
                        return None

                    if op != OP_DISPATCH:
                        continue

                    d = event.get("d") or {}

                    if t == "READY":
                        user = d.get("user") or {}
                        log(f"已就绪：bot = {user.get('username') or user.get('id')}")
                        log("如果机器人名字带「测试中」，说明还没上线，群聊可能收不到事件。")
                        continue

                    if t == "GROUP_AT_MESSAGE_CREATE":
                        gid = d.get("group_openid") or d.get("group_id")
                        if gid:
                            found["group_openid"] = gid
                            author = (d.get("author") or {}).get("member_openid", "?")
                            log("")
                            log(f"✅ 收到群消息！group_openid = {gid}")
                            log(f"   内容：{str(d.get('content'))[:60]}")
                            log(f"   发送者：{author}")
                            return gid

                    # 群消息但没 @（需要 GROUP_MESSAGE_CREATE 权限，通常没有）
                    if t == "GROUP_MESSAGE_CREATE":
                        gid = d.get("group_openid")
                        if gid:
                            log(f"（收到群内非 @ 消息，group_openid = {gid}）")
                            return gid
        except (TimeoutError, asyncio.TimeoutError):
            log("")
            log("⏰ 等待超时，没收到群消息。常见原因：")
            log("   1) 没在群里真的 @ 到机器人（要 @ 它，不是直接发消息）")
            log("   2) 机器人没被拉进群")
            log("   3) 机器人还在「开发中」状态，群聊事件不推送")
            log("   4) 群里 @ 了但机器人没开「群聊消息」权限")
            return None
        finally:
            hb.cancel()


def save_to_env(group_openid: str, env_key: str) -> None:
    env_path = COMMON_DIR / ".env"
    if not env_path.exists():
        log("找不到 .env，请手动把下面这行加进去：")
        log(f"{env_key}={group_openid}")
        return
    text = env_path.read_text(encoding="utf-8")
    if re.search(rf"^{re.escape(env_key)}=.*$", text, flags=re.M):
        text = re.sub(rf"^{re.escape(env_key)}=.*$", f"{env_key}={group_openid}",
                      text, flags=re.M)
    else:
        text += f"\n{env_key}={group_openid}\n"
    env_path.write_text(text, encoding="utf-8")
    log(f"已写入 {env_path.name}（{env_key}）")


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default=bots.DEFAULT_BOT, choices=bots.list_bots(),
                    help="要抓哪个机器人的群 openid（config.yaml 里 bots 的键名）")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        bot = bots.get_bot(args.bot, strict=False)
    except Exception as exc:  # noqa: BLE001
        log(f"❌ {exc}")
        return 1
    if not bot.appid or not bot.secret:
        names = bots.bot_env_names(args.bot)
        log(f"❌ 机器人「{bot.label}」还缺 {names['appid']} / {names['secret']}（写进 .env）")
        return 1

    gid = asyncio.run(run(args.bot, args.timeout, args.debug))
    if not gid:
        return 1

    save_to_env(gid, bots.bot_env_names(args.bot)["group_openid"])

    # 立刻验证：拿这个 openid 真发一条，确认整条链路可用
    log("")
    log("验证：用这个 group_openid 发一条测试消息 …")
    try:
        from send_qq import send_group_text

        send_group_text(gid, f"{bot.label}已就绪。", bot=args.bot)
        log("✅ 发送成功，群里应该已经收到测试消息")
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ 测试发送失败：{exc}")
        log("   openid 可能拿对了，但缺权限或 IP 不在白名单，按提示排查")
        return 1

    log("")
    handler = bots.bot_handler(args.bot)
    if handler == "menu":
        log(f"下一步：python menu/scripts/run_daily.py --bot {args.bot} --preview 看看要发的内容")
    elif handler == "reader":
        log(f"下一步：python reader/scripts/serve_reader.py --bot {args.bot}"
            "（常驻监听，去群里 @ 它就会回传 docx）")
    else:
        log(f"下一步：python common/scripts/run_text.py --bot {args.bot} --text \"测试通知\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
