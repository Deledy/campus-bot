"""生成一份操前小读 docx（只出文件，不发群）。

    # 看库里有哪些专题
    python reader/scripts/run_reader.py --list

    # 抽 2 个专题，生成到 reader/out/
    python reader/scripts/run_reader.py --build

    # 指定专题 / 指定标题，方便做样张
    python reader/scripts/run_reader.py --build --topics gz-inversion,gz-climate-factors

    # 只生成、不记进历史（做测试时用）
    python reader/scripts/run_reader.py --build --no-record
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
import topics as topiclib  # noqa: E402
from common import READER_DIR, ROOT, load_config, log  # noqa: E402
from docx_builder import build_docx  # noqa: E402


def reader_cfg() -> dict:
    cfg = load_config().get("reader") or {}
    return cfg if isinstance(cfg, dict) else {}


def out_path(cfg: dict, grade: str, book: str) -> Path:
    raw = str(cfg.get("out_dir") or "out").strip()
    directory = Path(raw) if Path(raw).is_absolute() else READER_DIR / raw
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = ["地理操前小读"] + [p for p in (book, grade) if p]
    return directory / ("_".join(parts) + f"_{stamp}.docx")


def build_issue(
    *,
    book: str | None = None,
    grade: str | None = None,
    count: int | None = None,
    max_lines: float | None = None,
    topics: list[str] | None = None,
    out: Path | None = None,
    record: bool = True,
) -> tuple[Path, list[dict]]:
    """按配置抽专题并生成 docx，返回 (文件路径, 本期专题)。

    参数为 None 时按 config.yaml 的 reader 段取默认值；供 serve_reader 复用。
    """
    cfg = reader_cfg()
    book = book or str(cfg.get("book") or "").strip() or None
    grade = grade or str(cfg.get("grade") or "").strip() or None
    count = int(count if count is not None else (cfg.get("per_issue") or 2))
    budget = max_lines if max_lines is not None else cfg.get("max_print_lines")
    budget = float(budget) if budget else 0.0
    library = topiclib.load_topics(book=book)

    if topics:
        by_id = {t["id"]: t for t in library}
        missing = [t for t in topics if t not in by_id]
        if missing:
            raise ValueError(f"专题库里没有：{'、'.join(missing)}")
        picked = [by_id[t] for t in topics]
    else:
        picked = topiclib.pick(
            library,
            count=count,
            grade=grade,
            avoid_recent=int(cfg.get("avoid_recent") or 10),
            max_lines=budget or None,
            budget=budget or None,
        )
    if not picked:
        raise ValueError("没有可用的专题（可能最近都出过，检查 reader.avoid_recent）")

    used = sum(topiclib.print_cost(t) for t in picked)
    if not budget:
        log(f"本次约 {used:.0f} 行（不限制版面，保持知识点完整）")
    elif used > budget:
        log(f"⚠️ 本次约 {used:.0f} 行，超过版面预算 {budget:g} 行")
    else:
        log(f"本次约 {used:.0f} 行（版面预算 {budget:g} 行）")

    target = out or out_path(cfg, grade or "", book or "")
    if not target.is_absolute():
        target = READER_DIR / target
    target.parent.mkdir(parents=True, exist_ok=True)
    build_docx(picked, target, cfg)
    log("本次出刊：" + "、".join(t["title"] for t in picked))
    shown = target.relative_to(ROOT) if target.is_relative_to(ROOT) else target
    log(f"已生成 {shown}（{target.stat().st_size // 1024} KB）")

    if record:
        topiclib.record([t["id"] for t in picked], target.name)
    return target, picked


def main() -> int:
    cfg = reader_cfg()

    ap = argparse.ArgumentParser(description="生成操前小读 docx")
    ap.add_argument("--list", action="store_true", help="列出专题库")
    ap.add_argument("--build", action="store_true", help="生成 docx")
    ap.add_argument("--topics", help="指定专题 id，逗号分隔")
    ap.add_argument("--book", help="只从这一册抽（如 必修一）；默认按 config.yaml 的 reader.book")
    ap.add_argument("--grade", help="只从这一年级抽；默认按 config.yaml 的 reader.grade")
    ap.add_argument("--count", type=int, help="本期出几个专题（默认按 config.yaml 的 reader.per_issue）")
    ap.add_argument("--max-lines", type=float, help="本次排版行数上限（默认按 config.yaml 的 reader.max_print_lines）")
    ap.add_argument("--no-record", action="store_true", help="不记入出刊历史")
    ap.add_argument("--out", help="输出文件路径（默认 reader/out/ 下按时间命名）")
    args = ap.parse_args()

    book = args.book or str(cfg.get("book") or "").strip() or None
    grade = args.grade or str(cfg.get("grade") or "").strip() or None

    if args.list:
        library = topiclib.load_topics(book=book)
        for topic in library:
            kind = "（含图）" if any(l.get("style") == "fig" for l in topic["lines"]) else ""
            log(f"{topic['id']:<22} {topic['book']:<8} {topic['grade']}  {topic['title']}{kind}")
        log(f"共 {len(library)} 个专题")
        return 0

    if not args.build:
        ap.print_help()
        return 0

    wanted = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else None
    try:
        build_issue(
            book=book,
            grade=grade,
            count=args.count,
            max_lines=args.max_lines,
            topics=wanted,
            out=Path(args.out) if args.out else None,
            record=not args.no_record,
        )
    except ValueError as exc:
        log(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
