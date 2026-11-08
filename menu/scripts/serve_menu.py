"""常驻服务：群里 @ 食谱机器人 → 回传本周完整食谱（每道菜带 emoji）。

    python menu/scripts/serve_menu.py --bot menu --preview   # 只打印本周消息，不连群
    python menu/scripts/serve_menu.py --bot menu             # 常驻监听

每日 09:50 的推送仍由 run_daily.py 负责；本脚本只处理群 @，
用 msg_id 做被动回复，不占主动消息额度，也不影响当天定时推送。

部署由 install.sh 生成 campus-bot-menu.service（常驻）
和 campus-bot-menu-push.service（每日定时调用 run_daily.py）。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
import bots  # noqa: E402
import doc_client  # noqa: E402
from common import load_env, log, today  # noqa: E402
from qq_listener import GroupListener  # noqa: E402
from send_qq import build_week_message, send_group_text  # noqa: E402

NO_DATA = "本周表格里还没有食谱数据。"


def week_range(day: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """本周一到周日（按北京时间）。"""
    target = day or today()
    start = target - dt.timedelta(days=target.weekday())
    return start, start + dt.timedelta(days=6)


def week_message(day: dt.date | None = None) -> str:
    """读腾讯文档，拼出本周所有已录入日期的食谱；没有数据时返回空字符串。"""
    start, end = week_range(day)
    week = doc_client.get_week() or {}

    days: dict[str, dict] = {}
    for item in week.get("days") or []:
        raw = str(item.get("date") or "")
        try:
            date = dt.date.fromisoformat(raw)
        except ValueError:
            continue
        if start <= date <= end:
            days[raw] = item

    return build_week_message(days, start, end)


class MenuService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.bot = bots.get_bot(name)
        self._lock = asyncio.Lock()

    async def run_forever(self) -> None:
        await GroupListener(self.name, self._on_at).run_forever()

    async def _on_at(self, d: dict) -> None:
        group = str(d.get("group_openid") or "")
        msg_id = str(d.get("id") or "")

        if self._lock.locked():
            await asyncio.to_thread(
                send_group_text, group, "正在读取本周食谱，稍等一下…",
                self.name, 15, msg_id, 1,
            )
            return

        async with self._lock:
            try:
                content = await asyncio.to_thread(week_message)
            except Exception as exc:  # noqa: BLE001
                log(f"读取本周食谱失败：{exc}")
                await asyncio.to_thread(
                    send_group_text, group, f"读取食谱失败：{exc}"[:300],
                    self.name, 15, msg_id, 1,
                )
                return

            content = content or NO_DATA
            await asyncio.to_thread(
                send_group_text, group, content, self.name, 15, msg_id, 1
            )
            log("已回传本周食谱")


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="常驻监听群 @，回传本周完整食谱")
    ap.add_argument("--bot", default="menu", choices=bots.list_bots())
    ap.add_argument("--preview", action="store_true", help="只打印本周消息，不连接群")
    args = ap.parse_args()

    if bots.bot_handler(args.bot) != "menu":
        log(f"机器人「{args.bot}」的 handler 不是 menu")
        return 1

    if args.preview:
        try:
            print(week_message() or NO_DATA)
        except Exception as exc:  # noqa: BLE001
            log(f"读取失败：{exc}")
            return 1
        return 0

    service = MenuService(args.bot)
    log(f"启动常驻监听：{service.bot.label}（{args.bot}）")
    try:
        asyncio.run(service.run_forever())
    except KeyboardInterrupt:
        log("已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
