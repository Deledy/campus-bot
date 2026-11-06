"""通用文本发送通道：把一段文字或一份稿子发到任意 text 类型机器人的群。

config.yaml 里 handler: text 的机器人都能用，不必为每个机器人写脚本：

    # 指定机器人，直接发一段文字
    python common/scripts/run_text.py --bot notice --text "明天下午 15:00 家长会"

    # 发该机器人 inbox 里最早的一份通知稿（.txt / .md）
    python common/scripts/run_text.py --bot notice

    # 只看要发什么，不真发
    python common/scripts/run_text.py --bot notice --preview

inbox 目录由 config.yaml 的 inbox 字段决定，默认 common/inbox/<键名>/。
发成功后，稿子会移到 <inbox>/processed/<时间戳>/，避免重复发送。
只配了一个 text 机器人时，--bot 可以省略。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import bots  # noqa: E402
from common import ROOT, load_config, log  # noqa: E402
from send_qq import send_message  # noqa: E402

SUFFIXES = (".txt", ".md")


def text_bots() -> list[str]:
    return [name for name in bots.list_bots() if bots.bot_handler(name) == "text"]


def resolve_bot(name: str | None) -> str:
    if name:
        return name
    names = text_bots()
    if len(names) == 1:
        return names[0]
    raise SystemExit(
        "有多个文本机器人，请用 --bot 指定其中一个：" + "、".join(names)
        if names
        else "config.yaml 里没有配置 handler: text 的机器人"
    )


def pick_notice(directory: Path) -> Path | None:
    """取 inbox 里最早放入的稿子。"""
    if not directory.exists():
        return None
    files = [p for p in directory.iterdir() if p.suffix.lower() in SUFFIXES]
    return min(files, key=lambda p: p.stat().st_mtime) if files else None


def archive(source: Path, directory: Path) -> None:
    dest = directory / "processed" / dt.datetime.now().strftime("%Y%m%d-%H%M")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / source.name
    source.replace(target)
    log(f"稿子已归档：{target.relative_to(ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="把文本发到指定 QQ 机器人所在的群")
    ap.add_argument("--bot", help="config.yaml 里 bots 的键名")
    ap.add_argument("--text", help="直接发送这段文字（不读 inbox）")
    ap.add_argument("--preview", action="store_true", help="只打印，不发送")
    args = ap.parse_args()

    name = resolve_bot(args.bot)
    if bots.bot_handler(name) != "text":
        raise SystemExit(f"机器人「{name}」的 handler 不是 text，不能用这个脚本")
    # 预览时允许凭据还没填；真发时缺凭据立刻报错
    bot = bots.get_bot(name, strict=not args.preview)

    source: Path | None = None
    directory: Path | None = None
    if args.text is not None:
        text = args.text.strip()
        if not text:
            log("--text 后面没有内容")
            return 1
    else:
        directory = bots.bot_inbox(name)
        source = pick_notice(directory)
        if source is None:
            log(f"{directory.relative_to(ROOT)} 里没有 .txt/.md 稿子 → 不发送")
            return 0
        text = source.read_text(encoding="utf-8-sig").strip()
        if not text:
            log(f"{source.name} 是空文件 → 不发送")
            return 0

    if args.preview:
        log(f"预览内容（{bot.label}）：\n" + "-" * 40 + f"\n{text}\n" + "-" * 40)
        return 0

    cfg = load_config()
    send_message(text, dry_run=cfg["send"].get("dry_run"), bot=name)

    if source is not None and directory is not None:
        archive(source, directory)
    log(f"{bot.label} 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
