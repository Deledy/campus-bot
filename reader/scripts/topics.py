"""专题库：加载 reader/knowledge/books/*/topics.json、轮转抽样、记录出刊历史。

一册一个文件，册清单在 reader/knowledge/books.json：

    reader/knowledge/books/必修一/topics.json
    {
      "book": "必修一", "code": "bx1", "grade": "高一",
      "topics": [ {...}, {...} ]
    }

专题是最小的出刊单位：一个标题 + 若干条要点（可带图片），
粒度到「章」（也可以到「节」或「复习专题」，由内容决定）。

抽样规则（全部确定性，不调模型）：
- 最近 avoid_recent 期出过的专题不再出现在候选池；
- 候选池里随机取 count 个；
- 候选池不足 count 个（整册快轮完了）时，重置候选池，允许重复。

历史存在 reader/data/reader_history.json，只保留最近 200 期。
"""
from __future__ import annotations

import json
import math
import re
import random
from pathlib import Path

from common import READER_DATA_DIR, READER_DIR, ROOT, log, read_json, today, write_json

BOOKS_FILE = READER_DIR / "knowledge" / "books.json"
BOOKS_DIR = READER_DIR / "knowledge" / "books"
ASSETS_DIR = READER_DIR / "knowledge" / "assets"
HISTORY_FILE = READER_DATA_DIR / "reader_history.json"
HISTORY_KEEP = 200

STYLES = ("h", "p", "sub", "list", "fig", "table")
ID_RE = re.compile(r"^[a-z0-9]+-[a-z0-9][a-z0-9-]*$")


class TopicError(RuntimeError):
    pass


def load_books() -> list[dict]:
    data = read_json(BOOKS_FILE, {})
    books = data.get("books") if isinstance(data, dict) else None
    if not books:
        raise TopicError(f"{BOOKS_FILE.relative_to(ROOT)} 里没有书籍清单（需要 books 数组）")
    for book in books:
        for field in ("code", "dir", "name", "grade"):
            if not str(book.get(field) or "").strip():
                raise TopicError(f"books.json 有一条缺少 {field}：{book}")
    return books


def book_dirs() -> list[str]:
    return [str(b["dir"]) for b in load_books()]


def _validate_topic(topic: dict, code: str, where: str) -> None:
    tid = str(topic.get("id") or "").strip()
    if not tid:
        raise TopicError(f"{where}：有专题缺少 id")
    if not ID_RE.match(tid):
        raise TopicError(f"{where}：专题 id「{tid}」格式不对（小写字母/数字/短横线）")
    if not tid.startswith(code + "-"):
        raise TopicError(f"{where}：专题 id「{tid}」应以「{code}-」开头")

    if not str(topic.get("title") or "").strip():
        raise TopicError(f"{where}：专题「{tid}」缺少 title")

    lines = topic.get("lines") or []
    if not lines:
        raise TopicError(f"{where}：专题「{tid}」没有内容行")

    for line in lines:
        style = line.get("style")
        if style not in STYLES:
            raise TopicError(
                f"{where}：专题「{tid}」有未知样式 {style!r}（只支持 {'/'.join(STYLES)}）"
            )
        if style == "fig":
            path = ASSETS_DIR / str(line.get("file") or "")
            if not line.get("file") or not path.exists():
                raise TopicError(f"{where}：专题「{tid}」引用的图片不存在：{path}")
        elif style == "table":
            rows = line.get("rows") or []
            if not rows or not any(any(str(c).strip() for c in row) for row in rows):
                raise TopicError(f"{where}：专题「{tid}」里有空表格")
        elif not str(line.get("text") or "").strip():
            raise TopicError(f"{where}：专题「{tid}」有空内容行")


def load_topics(book: str | None = None) -> list[dict]:
    """加载全部专题；book 传目录名（如「必修一」）则只加载那一册。"""
    manifest = {str(b["dir"]): b for b in load_books()}
    files = sorted(BOOKS_DIR.glob("*/topics.json"))
    if not files:
        raise TopicError(f"{BOOKS_DIR.relative_to(ROOT)} 下还没有任何专题文件")

    topics: list[dict] = []
    seen: set[str] = set()
    for path in files:
        dir_name = path.parent.name
        meta = manifest.get(dir_name)
        if meta is None:
            raise TopicError(f"目录「{dir_name}」不在 books.json 的清单里")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TopicError(f"{dir_name}/topics.json 不是合法 JSON：{exc}") from exc

        raw = payload.get("topics") if isinstance(payload, dict) else payload
        if not isinstance(raw, list) or not raw:
            raise TopicError(f"{dir_name}/topics.json 里没有专题（需要 topics 数组）")

        header_book = str(payload.get("book") or meta["name"])
        header_grade = str(payload.get("grade") or meta["grade"])

        for topic in raw:
            _validate_topic(topic, str(meta["code"]), f"{dir_name}/topics.json")
            tid = str(topic["id"])
            if tid in seen:
                raise TopicError(f"专题 id 重复：{tid}")
            seen.add(tid)
            topics.append(
                {
                    **topic,
                    "book": header_book,
                    "grade": header_grade,
                    "code": str(meta["code"]),
                }
            )

    if book:
        topics = [t for t in topics if t["book"] == book]
        if not topics:
            raise TopicError(f"「{book}」里还没有专题")
    return topics


def line_cost(line: dict, chars_per_line: int = 28) -> float:
    """单条内容行折算成打印行数：图片 8 行，表格按行数，文字按字宽。"""
    style = line.get("style")
    if style == "fig":
        return 8.0
    if style == "table":
        return len(line.get("rows") or []) * 1.2 + 1
    text = str(line.get("text") or "")
    return float(max(1, math.ceil(len(text) / chars_per_line)))


def print_cost(topic: dict, chars_per_line: int = 28) -> float:
    """估算专题在窄栏排版下占多少行（标题 2 行 + 各内容行）。

    这是选专题和拆分专题共用的版面预算口径，不追求精确，只求可比。
    """
    return 2.0 + sum(
        line_cost(line, chars_per_line) for line in topic.get("lines") or []
    )


def _history() -> list[dict]:
    data = read_json(HISTORY_FILE, {"issues": []})
    issues = data.get("issues") if isinstance(data, dict) else None
    return [i for i in (issues or []) if isinstance(i, dict)]


def recent_ids(limit: int = 10) -> list[str]:
    ids: list[str] = []
    for issue in _history()[-limit:]:
        ids.extend(str(t) for t in (issue.get("topics") or []))
    return ids


def pick(
    topics: list[dict],
    count: int = 2,
    grade: str | None = None,
    avoid_recent: int = 10,
    max_lines: float | None = None,
    budget: float | None = None,
    chars_per_line: int = 28,
) -> list[dict]:
    """抽题；max_lines 限制单个专题，budget 限制本次合计占多少行。

    先按 max_lines 过滤掉过长的专题；候选不够时退回全库。
    再按 budget 逐条累加，装不下就换下一条，保证一份小读不超版面。
    """
    pool = [t for t in topics if not grade or str(t.get("grade") or "") == grade]
    # 纯图片（如章节封面）不算知识点，抽题时排除；纯表格保留
    pool = [
        t for t in pool
        if any(line.get("style") != "fig" for line in t.get("lines") or [])
    ]
    if not pool:
        raise TopicError(f"没有年级为「{grade or '不限'}」的可用专题")

    if max_lines:
        fitting = [t for t in pool if print_cost(t, chars_per_line) <= max_lines]
        if fitting:
            pool = fitting
        else:
            log(f"没有 {max_lines:g} 行以内的「{grade or '不限'}」专题，退回全库抽取")

    blocked = set(recent_ids(avoid_recent))
    candidates = [t for t in pool if t["id"] not in blocked]
    if len(candidates) < count:
        log(f"候选池只剩 {len(candidates)} 个（已轮完一轮），本次允许重复")
        candidates = pool

    random.shuffle(candidates)
    picked: list[dict] = []
    used = 0.0
    for topic in candidates:
        cost = print_cost(topic, chars_per_line)
        if budget and picked and used + cost > budget:
            continue
        picked.append(topic)
        used += cost
        if len(picked) >= count:
            break

    return picked or [candidates[0]]


def record(topic_ids: list[str], filename: str = "") -> None:
    data = read_json(HISTORY_FILE, {"issues": []})
    issues = data.get("issues") if isinstance(data, dict) else []
    issues = [i for i in (issues or []) if isinstance(i, dict)]
    issues.append(
        {"date": today().isoformat(), "topics": list(topic_ids), "file": filename}
    )
    write_json(HISTORY_FILE, {"issues": issues[-HISTORY_KEEP:]})

    if filename:
        log(f"已记录出刊历史：{today().isoformat()} {'、'.join(topic_ids)}")
