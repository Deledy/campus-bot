"""把 source/ 里的 docx 抽取成纯文本 + 目录大纲 + 图片。

    python reader/scripts/extract_docx.py                 # 处理每一册
    python reader/scripts/extract_docx.py --book 必修一     # 只处理一册
    python reader/scripts/extract_docx.py --file x.docx --book 必修一

输出：
    reader/knowledge/work/text/<书名>_<文件名>.txt          正文，图片位置写成 [[img:必修一/bx1_img003.png::图注]]
    reader/knowledge/work/text/<书名>_<文件名>.outline.txt  目录大纲
    reader/knowledge/assets/<书名>/<册代码>_imgNNN.<ext>    图片实体（原图，按内容去重）

规则：
- 短边小于 100px 的图视为图标/符号，直接丢弃；
- 同一张图（内容哈希相同）只存一份；
- 图注优先取图下一行（以「图」开头或含「示意图/分布图」），否则取上一句短文本；
- 合订本用 --split-at 拆成两册，图片按所属册分别存放。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parents[2]
READER = ROOT / "reader"
SOURCE = READER / "knowledge" / "source"
OUT = READER / "knowledge" / "work" / "text"
ASSETS = READER / "knowledge" / "assets"
BOOKS_FILE = READER / "knowledge" / "books.json"

CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百]+章")
SECTION_RE = re.compile(r"^(?:第[一二三四五六七八九十百]+节|\d+\.\d+)")
HEADING_STYLE_RE = re.compile(r"^(?:Heading|标题)\s*(\d+)$", re.I)
CELL_SEP = " ｜ "
MIN_ICON_PX = 100

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def book_codes() -> dict[str, str]:
    data = json.loads(BOOKS_FILE.read_text(encoding="utf-8"))
    return {item["dir"]: item["code"] for item in data["books"]}


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def image_size(data: bytes) -> tuple[int, int]:
    """返回 (宽, 高) 像素；认不出的格式返回 (0, 0)。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return w, h
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return 0, 0


def iter_blocks(doc):
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def block_text(block) -> str:
    if isinstance(block, Table):
        rows = []
        for row in block.rows:
            cells = [" ".join(p.text.strip() for p in c.paragraphs if p.text.strip())
                     for c in row.cells]
            rows.append(CELL_SEP.join(cells))
        return "\n".join(r for r in rows if r.strip())
    return block.text.strip()


def heading_level(paragraph: Paragraph) -> int:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    m = HEADING_STYLE_RE.match(style.strip())
    if m:
        return int(m.group(1))
    text = paragraph.text.strip()
    if CHAPTER_RE.match(text):
        return 1
    if SECTION_RE.match(text):
        return 2
    return 0


class ImageStore:
    """按册存放图片，内容相同只写一次。"""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.saved = 0
        self.skipped = 0

    def save(self, book: str, code: str, data: bytes, suffix: str) -> str:
        digest = hashlib.sha1(data).hexdigest()
        if digest in self.seen:
            return self.seen[digest]

        directory = ASSETS / book
        directory.mkdir(parents=True, exist_ok=True)
        index = self.counters.get(book, 0) + 1
        self.counters[book] = index
        name = f"{code}_img{index:03d}{suffix}"
        (directory / name).write_bytes(data)
        rel = f"{book}/{name}"
        self.seen[digest] = rel
        self.saved += 1
        return rel


def pick_caption(blocks: list, idx: int) -> str:
    """图注：优先下一行（像图注），否则上一句短文本。"""
    for j in range(idx + 1, min(idx + 3, len(blocks))):
        text = block_text(blocks[j]).strip()
        if not text:
            continue
        compact = text.replace(" ", "")
        if len(text) <= 30 and (compact.startswith("图") or "示意图" in compact
                                or "分布图" in compact):
            return text.rstrip("。")
        break

    for j in range(idx - 1, max(idx - 3, -1), -1):
        text = block_text(blocks[j]).strip()
        if not text:
            continue
        # 上一行通常就是引出语，重复写会变成「图注＝正文」
        if len(text) <= 12 and not text.endswith(("。", "：", ":")) and not text.startswith("[["):
            return text
        break
    return ""


def extract(
    path: Path,
    book: str,
    code: str,
    split_at: str = "",
    book2: str = "",
    code2: str = "",
) -> list[dict]:
    doc = Document(str(path))
    rels = {}
    for rid, rel in doc.part.rels.items():
        rels[rid] = getattr(rel, "target_ref", "") or ""

    blocks = list(iter_blocks(doc))
    texts = [block_text(b) for b in blocks]
    store = ImageStore()

    marker = normalize(split_at) if split_at else ""
    split_index = -1
    if marker:
        hits = [i for i, text in enumerate(texts) if normalize(text) == marker]
        if not hits:
            print(f"⚠️ 找不到切分行「{split_at}」，不拆分")
        else:
            # 目录里也可能出现同名标题，取最后一次出现的位置
            split_index = hits[-1]

    outputs = {
        1: {"lines": [], "outline": [], "chapters": [], "sections": [], "book": book, "code": code},
        2: {"lines": [], "outline": [], "chapters": [], "sections": [], "book": book2, "code": code2},
    }

    for idx, block in enumerate(blocks):
        text = texts[idx]
        part = 2 if split_index >= 0 and idx >= split_index else 1
        bucket = outputs[part]

        if isinstance(block, Table):
            if text:
                bucket["lines"].append(text)
                bucket["lines"].append("")
            continue

        images = block._p.findall(".//" + qn("w:drawing"))

        if text:
            bucket["lines"].append(text)
            level = heading_level(block)
            if level:
                bucket["outline"].append("  " * (level - 1) + f"[H{level}] {text}")
                if level == 1:
                    bucket["chapters"].append(text)
                elif level == 2:
                    bucket["sections"].append(text)

        for drawing in images:
            blip = drawing.find(".//" + f"{{{A_NS}}}blip")
            if blip is None:
                continue
            rid = blip.get(f"{{{R_NS}}}embed")
            target = rels.get(rid or "", "")
            if not target:
                continue
            part_obj = doc.part.related_parts.get(rid)
            if part_obj is None:
                continue
            data = part_obj.blob
            width, height = image_size(data)
            if width and height and min(width, height) < MIN_ICON_PX:
                store.skipped += 1
                continue

            suffix = Path(target).suffix or ".png"
            rel = store.save(bucket["book"], bucket["code"], data, suffix)
            caption = pick_caption(blocks, idx)
            payload = f"[[img:{rel}::{caption}]]" if caption else f"[[img:{rel}]]"
            bucket["lines"].append(payload)

    results = []
    for part_no in (1, 2):
        bucket = outputs[part_no]
        if not bucket["lines"]:
            continue
        body = "\n".join(bucket["lines"]).strip() + "\n"
        stem = f"{bucket['book']}_{path.stem}"
        text_path = OUT / f"{stem}.txt"
        outline_path = OUT / f"{stem}.outline.txt"
        text_path.write_text(body, encoding="utf-8")
        outline_path.write_text("\n".join(bucket["outline"]) + "\n", encoding="utf-8")
        results.append(
            {
                "book": bucket["book"],
                "text": text_path,
                "outline": outline_path,
                "chars": len(body),
                "chapters": bucket["chapters"],
                "sections": bucket["sections"],
            }
        )

    results[0]["images_saved"] = store.saved
    results[0]["images_skipped"] = store.skipped
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="docx -> 文本 + 大纲 + 图片")
    ap.add_argument("--book", help="只处理某一册（目录名）")
    ap.add_argument("--file", help="只处理某个 docx")
    ap.add_argument("--split-at", help="从这一行起算第二册（用于合订本）")
    ap.add_argument("--book2", help="合订本的第二册目录名")
    args = ap.parse_args()

    codes = book_codes()

    if args.file:
        book = args.book or Path(args.file).parent.name
        jobs = [(book, Path(args.file))]
    else:
        roots = [SOURCE / args.book] if args.book else sorted(p for p in SOURCE.iterdir() if p.is_dir())
        jobs = []
        for root in roots:
            for path in sorted(root.glob("*.docx")):
                if not path.name.startswith("~$"):
                    jobs.append((root.name, path))

    if not jobs:
        print("没有找到 .docx（.doc 请先用 Word 另存为 .docx）")
        return 1

    for book, path in jobs:
        if book not in codes:
            print(f"跳过 {book}：不在 books.json 里")
            continue
        results = extract(
            path,
            book,
            codes[book],
            split_at=args.split_at or "",
            book2=args.book2 or "",
            code2=codes.get(args.book2 or "", ""),
        )
        for info in results:
            print(f"\n=== {info['book']} / {path.name}")
            print(f"  正文 {info['chars']} 字 | 章 {len(info['chapters'])} | 节 {len(info['sections'])}")
            if "images_saved" in info:
                print(f"  图片：保存 {info['images_saved']} 张，丢弃小图标 {info['images_skipped']} 张")
            print(f"  -> {info['text'].relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
