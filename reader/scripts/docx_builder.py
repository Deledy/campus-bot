"""专题 -> A4 docx 渲染器。只做排版，不判断内容对错。

版式（可在 config.yaml 的 reader 段覆盖）：
- A4 210×297mm，页边距由 margin_cm 控制（默认 2.0cm）
- 标题：黑体 12pt，左对齐、不加粗（靠「知识点N：」文字标识）
- 正文：宋体小四（12pt），单倍行距
- 标题全部顶格，靠 一、/（一）/1./（1）/① 区分层级
- 正文首行缩进 2 个全角空格，缩进写在文字里，复制后仍然保留
- 行样式：h=小标题，p=正文首行缩进两字，sub=整行缩进，fig=居中插图
- 图片默认宽 6cm、高上限 4cm；正文越多图自动缩得越小，保证不超出版面预算
- 图片缺失直接报错，不做占位（打印件不能缺图）
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.image import Image as DocxImage
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from topics import ASSETS_DIR

BODY_FONT = "宋体"
TITLE_FONT = "黑体"
LATIN_FONT = "Times New Roman"

DEFAULTS = {
    "title_pt": 12,
    "body_pt": 12,
    "line_spacing": 1.0,
    "margin_cm": 2.0,
    "figure_width_cm": 6,
    "figure_max_height_cm": 4,
}


def _set_font(run, *, east: str, size: float, bold: bool = False) -> None:
    """同时设置西文与中文（eastAsia）字体，否则 Word 里中文会回落到默认宋体。"""
    run.font.name = LATIN_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), east)


def _add_title(doc, text: str, cfg: dict, subtitle: str = "") -> None:
    """标题用普通段落 + 文字编号，不套 Word 标题样式，复制走也不会丢层级。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    _set_font(
        p.add_run(text), east=TITLE_FONT, size=cfg["title_pt"], bold=False
    )

    if subtitle:
        sub = doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.LEFT
        sub.paragraph_format.space_after = Pt(4)
        _set_font(
            sub.add_run(subtitle), east=BODY_FONT, size=cfg["body_pt"]
        )


def _add_table(doc, rows: list[list[str]], cfg: dict) -> None:
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_ALIGN_PARAGRAPH.CENTER
    table.autofit = True

    for r, row in enumerate(rows):
        for c in range(cols):
            cell = table.cell(r, c)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(1)
            paragraph.paragraph_format.space_after = Pt(1)
            text = str(row[c]) if c < len(row) else ""
            _set_font(
                paragraph.add_run(text),
                east=BODY_FONT,
                size=cfg["body_pt"],
                bold=(r == 0),
            )

    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)


def _picture_size(path: Path, cfg: dict) -> tuple[float, float]:
    """按宽度上限缩放；竖长图再按高度上限压一次，避免一张图吃掉整页。"""
    native = DocxImage.from_file(str(path))
    width_cm = float(cfg["figure_width_cm"])
    height_cm = width_cm * native.height / native.width

    max_h = float(cfg.get("figure_max_height_cm") or 0)
    if max_h and height_cm > max_h:
        height_cm = max_h
        width_cm = height_cm * native.width / native.height
    return width_cm, height_cm


def _add_figure(doc, line: dict, cfg: dict) -> None:
    path = ASSETS_DIR / str(line.get("file") or "")
    if not path.exists():
        raise FileNotFoundError(f"专题图片缺失：{path}")

    width_cm, _ = _picture_size(path, cfg)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(str(path), width=Cm(width_cm))

    caption = str(line.get("caption") or "").strip()
    if caption:
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.paragraph_format.space_after = Pt(4)
        _set_font(cap.add_run(caption), east=BODY_FONT, size=cfg["body_pt"])


def _add_line(doc, line: dict, cfg: dict) -> None:
    style = str(line.get("style") or "p")
    if style == "fig":
        _add_figure(doc, line, cfg)
        return
    if style == "table":
        _add_table(doc, line.get("rows") or [], cfg)
        return

    text = str(line.get("text") or "").strip()
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = cfg["line_spacing"]
    pf.space_before = Pt(3 if style == "h" else 0)
    pf.space_after = Pt(0)
    # 标题全部顶格；正文首行缩进 2 个全角空格，复制后缩进仍然保留
    if style in ("p", "sub", "list"):
        text = "\u3000\u3000" + text

    _set_font(
        p.add_run(text),
        east=BODY_FONT,
        size=cfg["body_pt"],
        bold=False,
    )


def _topic_cfg(topic: dict, cfg: dict) -> dict:
    """正文越多，图缩得越小；保证一条专题不会因为图太大而超出版面。"""
    text_lines = sum(
        1 for line in topic.get("lines") or []
        if line.get("style") in ("h", "p", "sub", "list")
    )
    if text_lines >= 16:
        scale = 0.6
    elif text_lines >= 10:
        scale = 0.8
    else:
        scale = 1.0
    if scale == 1.0:
        return cfg
    return {
        **cfg,
        "figure_width_cm": float(cfg["figure_width_cm"]) * scale,
        "figure_max_height_cm": float(cfg["figure_max_height_cm"]) * scale,
    }


def build_docx(topics: list[dict], out_path: Path, cfg: dict | None = None) -> Path:
    """把若干个专题渲染成一份 docx，返回写出的路径。"""
    cfg = {**DEFAULTS, **(cfg or {})}

    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    margin = Cm(float(cfg["margin_cm"]))
    section.top_margin = section.bottom_margin = margin
    section.left_margin = section.right_margin = margin

    normal = doc.styles["Normal"]
    normal.font.name = LATIN_FONT
    normal.font.size = Pt(cfg["body_pt"])
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.paragraph_format.line_spacing = cfg["line_spacing"]

    for i, topic in enumerate(topics):
        if i:
            doc.add_paragraph()
        topic_cfg = _topic_cfg(topic, cfg)
        subtitle = str(topic.get("subtitle") or "").strip()
        _add_title(doc, str(topic["title"]), cfg, subtitle)
        for line in topic.get("lines") or []:
            _add_line(doc, line, topic_cfg)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path
