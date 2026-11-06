"""专题库体检：把整个知识库扫一遍，报告结构问题和覆盖度。

    python reader/scripts/check_topics.py

检查项：
- 册目录是否都在 books.json 清单里
- 专题 id 唯一、格式合规、前缀与册代码一致
- 标题、内容行是否为空，样式是否认识
- 单条内容长度（20~80 字为正常，超过 120 字提示拆条）
- 图片是否真的存在
- 全库重复内容
- 每册覆盖度（已收录章节数 / 应有章节数）

有任何「错误」时退出码为 1，告警不影响。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
import topics as topiclib  # noqa: E402
from common import ROOT, log  # noqa: E402

ID_RE = re.compile(r"^[a-z0-9]+-[a-z0-9][a-z0-9-]*$")
STYLES = topiclib.STYLES
TEXT_MIN, TEXT_OK_MAX, TEXT_HARD_MAX = 8, 80, 120
LINES_MIN, LINES_MAX = 2, 40


def check() -> int:
    errors: list[str] = []
    warns: list[str] = []
    text_index: dict[str, list[str]] = {}

    try:
        books = topiclib.load_books()
    except topiclib.TopicError as exc:
        log(f"❌ {exc}")
        return 1

    manifest = {str(b["dir"]): b for b in books}
    files = sorted(topiclib.BOOKS_DIR.glob("*/topics.json"))
    if not files:
        log("❌ reader/knowledge/books 下还没有任何 topics.json")
        return 1

    ids: set[str] = set()
    coverage: list[str] = []

    for path in files:
        dir_name = path.parent.name
        meta = manifest.get(dir_name)
        if meta is None:
            errors.append(f"{dir_name}：目录不在 books.json 清单里")
            continue

        code = str(meta["code"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{dir_name}/topics.json：不是合法 JSON（{exc}）")
            continue

        raw = payload.get("topics") if isinstance(payload, dict) else payload
        if not isinstance(raw, list) or not raw:
            errors.append(f"{dir_name}/topics.json：没有专题")
            continue

        chapters: set[str] = set()
        for topic in raw:
            tid = str(topic.get("id") or "")
            title = str(topic.get("title") or "").strip()
            where = f"{dir_name}/{tid or '(无 id)'}"

            if not ID_RE.match(tid):
                errors.append(f"{where}：id 格式不对（小写字母/数字/短横线）")
            elif not tid.startswith(code + "-"):
                errors.append(f"{where}：id 前缀应为 {code}-")
            elif tid in ids:
                errors.append(f"{where}：id 重复")
            ids.add(tid)

            if not title:
                errors.append(f"{where}：缺少标题")
            elif len(title) > 30:
                warns.append(f"{where}：标题 {len(title)} 字，偏长")

            chapter = str(topic.get("chapter") or "").strip()
            if chapter:
                chapters.add(chapter)

            lines = topic.get("lines") or []
            body = [l for l in lines if l.get("style") != "fig"]
            if not lines:
                errors.append(f"{where}：没有任何内容行")
                continue
            if len(body) < LINES_MIN:
                warns.append(f"{where}：只有 {len(body)} 条内容，可能太单薄")
            if len(body) > LINES_MAX:
                warns.append(f"{where}：{len(body)} 条内容，建议拆成两个专题")

            for line in lines:
                style = line.get("style")
                if style not in STYLES:
                    errors.append(f"{where}：未知样式 {style!r}")
                    continue
                if style == "fig":
                    if not (topiclib.ASSETS_DIR / str(line.get("file") or "")).exists():
                        errors.append(f"{where}：图片不存在 {line.get('file')}")
                    continue

                if style == "table":
                    rows = line.get("rows") or []
                    if not rows or not any(any(str(c).strip() for c in row) for row in rows):
                        errors.append(f"{where}：有空表格")
                    continue

                text = str(line.get("text") or "").strip()
                if not text:
                    errors.append(f"{where}：有空内容行")
                    continue
                if len(text) > TEXT_HARD_MAX:
                    errors.append(f"{where}：一条 {len(text)} 字，超过 {TEXT_HARD_MAX}，请拆条")
                elif len(text) > TEXT_OK_MAX:
                    warns.append(f"{where}：一条 {len(text)} 字，建议拆条")

                if len(text) >= 12:
                    text_index.setdefault(text, []).append(tid)

        expected = meta.get("chapters")
        if expected:
            coverage.append(
                f"{dir_name}：{len(chapters)}/{expected} 章"
                + ("" if len(chapters) >= int(expected) else "  ← 还有缺章")
            )
        else:
            coverage.append(f"{dir_name}：{len(raw)} 个专题（未按章组织）")

    duplicates = {
        text: owners for text, owners in text_index.items() if len(set(owners)) > 1
    }
    for text, owners in list(duplicates.items())[:10]:
        warns.append(f"内容重复：{'、'.join(sorted(set(owners)))} → 「{text[:30]}」")

    log("覆盖度：")
    for line in coverage:
        log("  " + line)

    if warns:
        log(f"\n⚠️ 告警 {len(warns)} 条：")
        for item in warns[:30]:
            log("  - " + item)
        if len(warns) > 30:
            log(f"  …… 其余 {len(warns) - 30} 条略")

    if errors:
        log(f"\n❌ 错误 {len(errors)} 条（必须先修）：")
        for item in errors:
            log("  - " + item)
        return 1

    log(f"\n✅ 通过：{len(ids)} 个专题，id 全部唯一")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
