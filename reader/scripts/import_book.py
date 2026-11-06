"""按知识点把抽取好的正文切成专题，生成 topics.json 草稿。

    python reader/scripts/import_book.py --book 必修一
    python reader/scripts/import_book.py --book 必修一 --write   # 直接覆盖正式库

切分单位：一个「一、」知识块 = 一份；块太长（默认 >1000 字）再按「（一）」拆，
还长就按句子打包。章、节只作为归属信息（chapter / subtitle），不单独出刊。

章节骨架来自 reader/knowledge/outline/<书名>.json —— 下载的总结标题格式不统一，
不能靠正则硬切，必须按人教版目录登记。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
from common import READER_DIR, ROOT, log, write_json  # noqa: E402

TEXT_DIR = READER_DIR / "knowledge" / "work" / "text"
OUTLINE_DIR = READER_DIR / "knowledge" / "outline"
DRAFT_DIR = READER_DIR / "knowledge" / "work" / "json"
BOOKS_FILE = READER_DIR / "knowledge" / "books.json"

SECTION_RE = re.compile(r"^(?:第[一二三四五六七八九十]+节|\d+\.\d+)")
A_RE = re.compile(r"^[一二三四五六七八九十]+、\s*")
B_RE = re.compile(r"^（[一二三四五六七八九十]+）\s*")
NUM_PREFIX_RE = re.compile(r"^\d+[.、：:]\s*")
CELL_SEP = " ｜ "
IMG_RE = re.compile(r"^\[\[img:([^:\]]+)(?:::(.*?))?\]\]$", re.S)


def load_book_meta(book: str) -> dict:
    data = json.loads(BOOKS_FILE.read_text(encoding="utf-8"))
    for item in data["books"]:
        if item["dir"] == book:
            return item
    raise SystemExit(f"books.json 里没有「{book}」")


def text_file(book: str) -> Path:
    files = [
        p for p in TEXT_DIR.glob(f"{book}_*.txt")
        if not p.name.endswith(".outline.txt")
    ]
    if not files:
        raise SystemExit(f"{TEXT_DIR.relative_to(ROOT)} 里没有 {book} 的文本，先跑 extract_docx.py")
    return files[0]


def pack(parts: list[str], limit: int) -> list[str]:
    out, buf = [], ""
    for part in parts:
        if buf and len(buf) + len(part) > limit:
            out.append(buf)
            buf = part
        else:
            buf += part
    if buf:
        out.append(buf)
    return out


def split_long(text: str, limit: int = 110) -> list[str]:
    if len(text) <= limit:
        return [text]
    for pattern in (r"(?<=[。；！？])", r"(?<=，)"):
        parts = [p for p in re.split(pattern, text) if p]
        if len(parts) > 1:
            packed = pack(parts, limit)
            if all(len(p) <= limit + 30 for p in packed):
                return packed
    return [text[i:i + limit] for i in range(0, len(text), limit)]


NUM_HEAD_RE = re.compile(r"^(?:\d+[.、]|（\d+）|\(\d+\))")


def looks_like_heading(text: str) -> bool:
    """判断一行是不是小标题：编号短句，或极短的无标点短语。"""
    if B_RE.match(text):
        return True
    if NUM_HEAD_RE.match(text) and "。" not in text and len(text) <= 14:
        return True
    if len(text) <= 12 and not any(c in text for c in "。，；！？"):
        return True
    return False


def shorten_title(text: str, limit: int = 24) -> str:
    """标题太长（原文把标题和正文写在同一行）时，截到第一个冒号/句号处。"""
    title = text.strip()
    for sep in ("：", ":", "。", "；", "，", ","):
        idx = title.find(sep)
        if 0 < idx <= limit:
            title = title[:idx]
            break
    return title if len(title) <= limit else title[:limit] + "…"


def parse_lines(raw: list[str]) -> list[dict]:
    """正文行 -> 渲染行。连续的「 ｜ 」行合并成一张表。"""
    out: list[dict] = []
    i = 0
    while i < len(raw):
        text = raw[i].strip()
        if not text:
            i += 1
            continue

        img = IMG_RE.match(text)
        if img:
            out.append(
                {
                    "style": "fig",
                    "file": img.group(1).strip(),
                    "caption": (img.group(2) or "").strip(),
                }
            )
            i += 1
            continue

        if CELL_SEP in text:
            rows = []
            while i < len(raw) and CELL_SEP in raw[i]:
                cells = [c.strip() for c in raw[i].split(CELL_SEP)]
                if any(cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append({"style": "table", "rows": rows})
            continue

        if looks_like_heading(text):
            out.append({"style": "h", "text": text})
        else:
            out.extend({"style": "p", "text": piece} for piece in split_long(text))
        i += 1
    return out


def clean_heading(text: str) -> str:
    """去掉「第二节」「2.1」这类编号，合并多余空格，去掉残留的冒号。"""
    cleaned = SECTION_RE.sub("", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).lstrip("：:、· ")
    return cleaned or text.strip()


def split_sections(body: list[str], chapter_title: str):
    """按「第X节」或「2.1」切段；没有节标题时整章视为一节。"""
    marks = [i for i, line in enumerate(body) if SECTION_RE.match(line.strip())]
    if not marks:
        return [(1, chapter_title, body)]

    sections = []
    if marks[0] > 0:
        sections.append((1, chapter_title, body[:marks[0]]))
    for j, start in enumerate(marks):
        end = marks[j + 1] if j + 1 < len(marks) else len(body)
        sections.append(
            (len(sections) + 1, shorten_title(clean_heading(body[start])),
             body[start + 1:end])
        )
    return sections


LEAD_NUM_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百]+[章节]|[一二三四五六七八九十]+、|"
    r"（[一二三四五六七八九十\d]+）|\([一二三四五六七八九十\d]+\)|\d+[.、])\s*"
)


def normalize(text: str) -> str:
    """比对标题时忽略编号：四、海岸地貌 == 海岸地貌；3.地球自转的地理意义 == 地球自转的地理意义。"""
    text = LEAD_NUM_RE.sub("", re.sub(r"\s+", "", text))
    return text.rstrip("：:")


def split_blocks(body: list[str], explicit: list[str] | None = None):
    """按「一、」切知识块；没有编号时用 outline 里登记的标题表切。"""
    if explicit:
        marks = []
        for name in explicit:
            idx = next(
                (i for i, line in enumerate(body) if normalize(line) == normalize(name)),
                None,
            )
            if idx is not None:
                marks.append(idx)
        marks = sorted(set(marks))
        if not marks:
            marks = [i for i, line in enumerate(body) if A_RE.match(line.strip())]
    else:
        marks = [i for i, line in enumerate(body) if A_RE.match(line.strip())]

    if not marks:
        return [("", body)]

    blocks = []
    if marks[0] > 0:
        blocks.append(("", body[:marks[0]]))
    for j, start in enumerate(marks):
        end = marks[j + 1] if j + 1 < len(marks) else len(body)
        blocks.append((body[start].strip(), body[start + 1:end]))
    return blocks


def text_chars(topic: dict) -> int:
    return sum(len(l.get("text") or "") for l in topic["lines"])


def merge_small(topics: list[dict], min_chars: int = 80) -> list[dict]:
    """把几乎没内容的碎片（只有一句引语、只剩标题）并进下一条。"""
    merged: list[dict] = []
    carry: list[dict] = []
    for topic in topics:
        if carry:
            topic["lines"] = carry + topic["lines"]
            carry = []
        has_table = any(l.get("style") in ("table", "fig") for l in topic["lines"])
        if text_chars(topic) < min_chars and not has_table:
            carry = topic["lines"]
            continue
        merged.append(topic)

    if carry:
        if merged:
            merged[-1]["lines"].extend(carry)
        else:
            merged.append({"lines": carry})
    return merged


def block_chars(raw: list[str]) -> int:
    return sum(len(l.strip()) for l in raw)


def build_topics(book: str, max_chars: int) -> list[dict]:
    meta = load_book_meta(book)
    outline = json.loads((OUTLINE_DIR / f"{book}.json").read_text(encoding="utf-8"))
    lines = [l.rstrip() for l in text_file(book).read_text(encoding="utf-8").splitlines()]

    starts = []
    for chapter in outline["chapters"]:
        idx = None
        for i, line in enumerate(lines):
            s = line.strip()
            if s and len(s) <= 32 and any(m in s for m in chapter["match"]):
                idx = i
                break
        if idx is None:
            raise SystemExit(f"找不到章标题：{chapter['title']}（匹配 {chapter['match']}）")
        starts.append((chapter, idx))

    topics: list[dict] = []
    for ci, (chapter, start) in enumerate(starts):
        end = starts[ci + 1][1] if ci + 1 < len(starts) else len(lines)
        begin = start if chapter.get("include_match_line") else start + 1
        body = [l for l in lines[begin:end] if l.strip()]
        chapter_topics: list[dict] = []

        for sec_no, sec_title, sec_body in split_sections(body, chapter["title"]):
            seq = 0
            for heading, raw in split_blocks(sec_body, chapter.get("blocks")):
                title = clean_heading(A_RE.sub("", heading).strip())
                title = NUM_PREFIX_RE.sub("", title).strip().rstrip("：:") or sec_title
                title = shorten_title(title)
                chunks = []

                if block_chars(raw) > max_chars:
                    subs = [(i, l.strip()) for i, l in enumerate(raw)
                            if B_RE.match(l.strip()) or A_RE.match(l.strip())]
                    if len(subs) > 1:
                        first = subs[0][0]
                        if first > 0:
                            chunks.append((title, raw[:first]))
                        for k, (idx, sub_head) in enumerate(subs):
                            last = subs[k + 1][0] if k + 1 < len(subs) else len(raw)
                            sub_title = (
                                B_RE.sub("", sub_head)
                                if B_RE.match(sub_head) else A_RE.sub("", sub_head)
                            ).strip().rstrip("：:")
                            chunks.append((f"{title}·{sub_title}", raw[idx + 1:last]))
                    else:
                        parsed = parse_lines(raw)
                        buffer: list[dict] = []
                        size = 0
                        for line in parsed:
                            weight = len(line.get("text") or "")
                            if buffer and size + weight > max_chars:
                                chunks.append((title, buffer))
                                buffer, size = [], 0
                            buffer.append(line)
                            size += weight
                        if buffer:
                            chunks.append((title, buffer))
                else:
                    chunks.append((title, raw))

                # 同一标题被切成多片（原文没有更细的标题）时，加（1）（2）区分
                if len(chunks) > 1:
                    counts = {}
                    for chunk_title, _ in chunks:
                        counts[chunk_title] = counts.get(chunk_title, 0) + 1
                    numbered, seen = [], {}
                    for chunk_title, chunk_raw in chunks:
                        if counts[chunk_title] > 1:
                            seen[chunk_title] = seen.get(chunk_title, 0) + 1
                            chunk_title = f"{chunk_title}（{seen[chunk_title]}）"
                        numbered.append((chunk_title, chunk_raw))
                    chunks = numbered

                for k, (chunk_title, chunk_raw) in enumerate(chunks):
                    seq += 1
                    lines_parsed = (
                        chunk_raw if chunk_raw and isinstance(chunk_raw[0], dict)
                        else parse_lines(chunk_raw)
                    )
                    if not lines_parsed:
                        seq -= 1
                        continue
                    chapter_topics.append(
                        {
                            "id": f"{meta['code']}-{chapter['no']:02d}{sec_no:02d}-{seq:02d}",
                            "chapter": chapter["title"],
                            "section": sec_title,
                            "title": chunk_title,
                            "subtitle": f"{book} · {chapter['title']}",
                            "lines": lines_parsed,
                        }
                    )
        topics.extend(merge_small(chapter_topics))
    return topics


def main() -> int:
    ap = argparse.ArgumentParser(description="正文 -> 专题 JSON 草稿")
    ap.add_argument("--book", required=True)
    ap.add_argument("--max-chars", type=int, default=1000)
    ap.add_argument("--write", action="store_true", help="写入正式库（默认只写草稿）")
    args = ap.parse_args()

    meta = load_book_meta(args.book)
    topics = build_topics(args.book, args.max_chars)

    payload = {
        "book": meta["name"],
        "code": meta["code"],
        "grade": meta["grade"],
        "topics": topics,
    }
    target = (
        READER_DIR / "knowledge" / "books" / args.book / "topics.json"
        if args.write
        else DRAFT_DIR / f"{args.book}.json"
    )
    write_json(target, payload)

    total = sum(len(l.get("text") or "") for t in topics for l in t["lines"])
    log(f"{args.book}：{len(topics)} 个专题，正文 {total} 字")
    for topic in topics:
        chars = sum(len(l.get("text") or "") for l in topic["lines"])
        tables = sum(1 for l in topic["lines"] if l.get("style") == "table")
        flag = "⚠️" if chars > args.max_chars * 1.2 else "  "
        log(f"  {flag} {topic['id']}  {chars:>4}字 表{tables}  {topic['title'][:28]}")
    log(f"-> {target.relative_to(ROOT)}")
    if not args.write:
        log("这是草稿，确认后加 --write 写进正式库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
