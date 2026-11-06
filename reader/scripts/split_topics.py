"""按册给整块知识点编号，保持原文内容完整，不拆分。

一个知识点 = 教材原文里的一个完整知识块（原来的「一、」块）。
本脚本只做两件事：
1. 标题改成「知识点N：原标题」，N 按册内顺序；
2. 每个知识点只保留第一张图（主图）。

不按行数、不按子标题拆分，避免把「洪涝灾害」拆成「3.原因」「4.危害」这种碎片。

用法：
    python reader/scripts/split_topics.py              # 只报告
    python reader/scripts/split_topics.py --write      # 先备份，再写回
    python reader/scripts/split_topics.py --book 必修一 --write
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "common" / "py"))
import topics as topiclib  # noqa: E402
from common import READER_DIR, log, write_json  # noqa: E402

BACKUP_DIR = READER_DIR / "knowledge" / "work" / "backup"
TRAILING_NUM = re.compile(r"（\d+）\s*$")


def trim_figures(lines: list[dict]) -> tuple[list[dict], int]:
    """每个知识点只保留第一张图，返回（新行，去掉的图数）。"""
    kept = False
    dropped = 0
    result = []
    for line in lines:
        if line.get("style") == "fig":
            if kept:
                dropped += 1
                continue
            kept = True
        result.append(line)
    return result, dropped


def main() -> int:
    ap = argparse.ArgumentParser(description="给整块知识点编号（不拆分）")
    ap.add_argument("--book", help="只处理这一册（目录名，如 必修一）")
    ap.add_argument("--write", action="store_true", help="写回 topics.json（默认只报告）")
    args = ap.parse_args()

    books = topiclib.load_books()
    if args.book:
        books = [b for b in books if str(b["dir"]) == args.book]
        if not books:
            raise SystemExit(f"books.json 里没有「{args.book}」")

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    total = total_dropped = 0

    for book in books:
        path = topiclib.BOOKS_DIR / str(book["dir"]) / "topics.json"
        if not path.exists():
            continue

        payload = json.loads(path.read_text(encoding="utf-8"))
        topics = payload.get("topics") or []
        if all(str(t.get("title") or "").startswith("知识点") for t in topics):
            log(f"{book['dir']}：已经编号，跳过")
            continue

        dropped = 0
        for number, topic in enumerate(topics, 1):
            title = TRAILING_NUM.sub("", str(topic.get("title") or "")).strip()
            topic["title"] = f"知识点{number}：{title}"
            topic["lines"], cut = trim_figures(topic.get("lines") or [])
            dropped += cut

        total += len(topics)
        total_dropped += dropped
        log(f"{book['dir']}：{len(topics)} 个知识点（去掉多余图片 {dropped} 张）")

        if args.write:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup = BACKUP_DIR / f"{book['dir']}-topics-before-number-{stamp}.json"
            shutil.copy2(path, backup)
            payload["topics"] = topics
            write_json(path, payload)
            log(f"  已备份：{backup.relative_to(READER_DIR)}")

    log(f"合计：{total} 个知识点；去掉多余图片 {total_dropped} 张")
    if not args.write:
        log("这是预览，确认后加 --write 写回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
