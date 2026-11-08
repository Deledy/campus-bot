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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
import bots  # noqa: E402
import run_reader  # noqa: E402
from common import load_env, log  # noqa: E402
from qq_listener import GroupListener  # noqa: E402
from send_qq import send_group_file, send_group_text  # noqa: E402

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

    async def run_forever(self) -> None:
        await GroupListener(self.name, self._on_at).run_forever()

    async def _on_at(self, d: dict) -> None:
        group = str(d.get("group_openid") or "")
        msg_id = str(d.get("id") or "")
        content = str(d.get("content") or "").strip()

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
