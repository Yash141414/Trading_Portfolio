"""
Build one PDF from the written analysis and the generated backtest report:

  Part 1  results/ANALYSIS.md   (written interpretation)
  Part 2  results/report.md     (full statistics, charts, breakdowns)

  python make_pdf.py                      # -> results/Q2_Report.pdf
  python make_pdf.py --out some/file.pdf
  python make_pdf.py doc.md [more.md ...] --out doc.pdf   # any Markdown file(s) -> one PDF

Re-run after run_backtest.py so the PDF matches the latest results. It handles the
Markdown these two files use: headings, paragraphs, nested bullets, numbered lists,
block quotes, pipe tables, images, and inline bold / italic / code / links.
"""
import argparse
import re
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

RESULTS = Path("results")
FONTS = Path("C:/Windows/Fonts")
PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
TEXT_W = PAGE_W - 2 * MARGIN
ACCENT = colors.HexColor("#1f4e79")
GRID = colors.HexColor("#c8cfd8")
HEAD_BG = colors.HexColor("#e8eef5")
ZEBRA = colors.HexColor("#f7f9fb")


def register_fonts() -> tuple[str, str]:
    """Arial covers the − ≥ ± → × – characters the analysis uses; the built-in Helvetica does not."""
    try:
        for name, file in (("Body", "arial.ttf"), ("Body-Bold", "arialbd.ttf"),
                           ("Body-Italic", "ariali.ttf"), ("Body-BoldItalic", "arialbi.ttf"),
                           ("Mono", "consola.ttf")):
            pdfmetrics.registerFont(TTFont(name, str(FONTS / file)))
        pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic",
                                      boldItalic="Body-BoldItalic")
        return "Body", "Mono"
    except Exception:
        return "Helvetica", "Courier"


BODY, MONO = register_fonts()


def styles() -> dict[str, ParagraphStyle]:
    """Paragraph styles used throughout the PDF."""
    base = ParagraphStyle("body", fontName=BODY, fontSize=9.5, leading=13.2, spaceAfter=5)
    return {
        "body": base,
        "title": ParagraphStyle("title", base, fontSize=20, leading=25, textColor=ACCENT, spaceAfter=6),
        "part": ParagraphStyle("part", base, fontSize=11, leading=14, textColor=colors.HexColor("#555555"),
                               spaceAfter=14),
        "h1": ParagraphStyle("h1", base, fontSize=16, leading=20, textColor=ACCENT, spaceBefore=4, spaceAfter=8),
        "h2": ParagraphStyle("h2", base, fontName=BODY + "-Bold" if BODY == "Body" else "Helvetica-Bold",
                             fontSize=12.5, leading=16, textColor=ACCENT, spaceBefore=10, spaceAfter=5),
        "h3": ParagraphStyle("h3", base, fontName=BODY + "-Bold" if BODY == "Body" else "Helvetica-Bold",
                             fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=4),
        "quote": ParagraphStyle("quote", base, fontSize=9.3, leading=13, textColor=colors.HexColor("#5a3d00")),
        "bullet": ParagraphStyle("bullet", base, spaceAfter=2),
        "caption": ParagraphStyle("caption", base, fontSize=8, textColor=colors.HexColor("#666666"),
                                  alignment=TA_CENTER, spaceAfter=8),
        "cover_note": ParagraphStyle("cover_note", base, fontSize=10, leading=14),
    }


S = styles()


def inline(text: str) -> str:
    """Markdown inline -> reportlab mini-HTML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", lambda m: f'<font name="{MONO}" size="8.5">{m.group(1)}</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"!?\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    return text


def column_widths(rows: list[list[str]], size: float) -> list[float]:
    """Every column gets at least its longest word (no mid-word breaks); the rest of the page width
    goes to columns in proportion to how much wider their full text would like to be."""
    pad = 8
    plain = [[re.sub(r"[*`]|\[([^\]]*)\]\([^)]*\)", r"\1", c) for c in r] for r in rows]
    bold = BODY + "-Bold" if BODY == "Body" else "Helvetica-Bold"
    def w(text, i):
        return pdfmetrics.stringWidth(text, bold if i == 0 else BODY, size)
    ncol = len(rows[0])
    need = [max(w(word, i) for i, r in enumerate(plain) for word in (r[c].split() or [""])) + pad
            for c in range(ncol)]
    want = [min(max(w(r[c], i) for i, r in enumerate(plain)) + pad, TEXT_W * 0.45) for c in range(ncol)]
    want = [max(a, b) for a, b in zip(want, need)]
    if sum(need) >= TEXT_W:
        return [n * TEXT_W / sum(need) for n in need]
    if sum(want) <= TEXT_W:
        return [x * TEXT_W / sum(want) for x in want]
    spare, extra = TEXT_W - sum(need), [x - n for x, n in zip(want, need)]
    return [n + spare * e / sum(extra) for n, e in zip(need, extra)]


def table_flowable(rows: list[list[str]]) -> Table:
    """A Markdown pipe table (list of rows) -> a styled reportlab Table with a repeating header."""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    size = 8.2 if ncol <= 4 else 7.4 if ncol <= 6 else 6.4
    cell = ParagraphStyle("cell", S["body"], fontSize=size, leading=size * 1.25, spaceAfter=0)
    head = ParagraphStyle("head", cell, fontName=BODY + "-Bold" if BODY == "Body" else "Helvetica-Bold")
    widths = column_widths(rows, size)
    data = [[Paragraph(inline(c), head if i == 0 else cell) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [("GRID", (0, 0), (-1, -1), 0.4, GRID), ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2.2),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2), ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
             ("RIGHTPADDING", (0, 0), (-1, -1), 3.5)]
    style += [("BACKGROUND", (0, i), (-1, i), ZEBRA) for i in range(2, len(data), 2)]
    t.setStyle(TableStyle(style))
    return t


def image_flowable(path: Path, alt: str) -> list:
    """A chart image scaled to the text width (max 120 mm tall) with its caption, kept together."""
    if not path.exists():
        return [Paragraph(f"[missing chart: {path.name}]", S["caption"])]
    w, h = ImageReader(str(path)).getSize()
    width = TEXT_W
    height = width * h / w
    if height > 120 * mm:
        height, width = 120 * mm, 120 * mm * w / h
    return [KeepTogether([Image(str(path), width=width, height=height), Paragraph(inline(alt), S["caption"])])]


def list_flowable(items: list[tuple[int, str]], ordered: bool) -> ListFlowable:
    """items: (level, text). Level-1 items nest under the preceding level-0 item."""
    top, i = [], 0
    while i < len(items):
        lvl, text = items[i]
        children = []
        j = i + 1
        while j < len(items) and items[j][0] > lvl:
            children.append(ListItem(Paragraph(inline(items[j][1]), S["bullet"]), leftIndent=12))
            j += 1
        flow = [Paragraph(inline(text), S["bullet"])]
        if children:
            flow.append(ListFlowable(children, bulletType="bullet", start="–", leftIndent=12,
                                     bulletFontName=BODY, bulletFontSize=8))
        top.append(ListItem(flow, leftIndent=14))
        i = j
    kw = dict(bulletType="1", bulletFormat="%s.") if ordered else dict(bulletType="bullet", start="•")
    return ListFlowable(top, leftIndent=14, bulletFontName=BODY, bulletFontSize=9, spaceAfter=5, **kw)


def md_to_flowables(md: str, base_dir: Path, skip_title: bool) -> list:
    """Convert one Markdown document into a list of reportlab flowables."""
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            text = inline(s[level:].strip())
            if level == 1:
                if not skip_title:
                    out.append(Paragraph(text, S["h1"]))
            else:
                out.append(Paragraph(text, S["h2" if level == 2 else "h3"]))
            i += 1
            continue
        if s.startswith("!["):
            m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", s)
            out += image_flowable(base_dir / m.group(2), m.group(1))
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            t = table_flowable(rows)
            out += [KeepTogether([t]) if len(rows) <= 15 else t, Spacer(1, 6)]
            continue
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            box = Table([[Paragraph(inline(" ".join(buf)), S["quote"])]], colWidths=[TEXT_W])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff4e0")),
                                     ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#e0a030")),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                                     ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
            out += [box, Spacer(1, 8)]
            continue
        m_b, m_n = re.match(r"^(\s*)- (.*)", line), re.match(r"^(\s*)\d+\. (.*)", line)
        if m_b or m_n:
            ordered = bool(m_n) and not m_b
            items = []
            while i < len(lines):
                ln = lines[i]
                mb, mn = re.match(r"^(\s*)- (.*)", ln), re.match(r"^(\s*)\d+\. (.*)", ln)
                m = mb or mn
                if m:
                    items.append((1 if len(m.group(1)) >= 2 else 0, m.group(2).strip()))
                elif ln.strip() and ln.startswith(" ") and items:
                    lvl, t = items[-1]
                    items[-1] = (lvl, t + " " + ln.strip())
                else:
                    break
                i += 1
            out.append(list_flowable(items, ordered))
            continue
        buf = []
        while i < len(lines):
            t = lines[i].strip()
            if not t or t.startswith(("#", "|", "!", ">")) or re.match(r"^(\s*)(- |\d+\. )", lines[i]):
                break
            buf.append(t)
            i += 1
        out.append(Paragraph(inline(" ".join(buf)), S["body"]))
    return glue_headings(out)


def block_height(f) -> float:
    """Rendered height of a flowable at full text width (40 mm if it can't be measured here)."""
    try:
        if isinstance(f, KeepTogether):
            return sum(x.wrap(TEXT_W, PAGE_H)[1] for x in f._content)
        return f.wrap(TEXT_W, PAGE_H)[1]
    except Exception:
        return 40 * mm


def is_lead(f) -> bool:
    """A heading (h2/h3) or a short body line ending in ':' that introduces the block after it."""
    if not isinstance(f, Paragraph):
        return False
    if f.style.name in ("h2", "h3"):
        return True
    text = re.sub(r"<[^>]+>", "", f.text).strip()
    return f.style.name == "body" and len(text) < 80 and text.endswith(":")


def glue_headings(flows: list) -> list:
    """Before each heading or lead-in line, start a new page only if it plus everything up to and
    including the next real block (chaining through consecutive headings, capped at 150 mm) won't
    fit in the space left - so a heading or lead-in never ends a page on its own."""
    out = []
    for i, f in enumerate(flows):
        if is_lead(f) and (i == 0 or not is_lead(flows[i - 1])) and i + 1 < len(flows):
            need, j = 12 * mm, i + 1
            while j < len(flows):
                need += block_height(flows[j])
                if not is_lead(flows[j]):
                    break
                j += 1
            out.append(CondPageBreak(min(need, 150 * mm)))
        out.append(f)
    return out


def first_heading(md: str) -> str:
    """The document's first '# ' heading (its title)."""
    m = re.search(r"^# (.+)$", md, re.M)
    return m.group(1).strip() if m else ""


def on_page(canvas, doc):
    """Footer on every page: document name and page number."""
    canvas.saveState()
    canvas.setFont(BODY, 7.5)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(MARGIN, 10 * mm, "Quant Camp Q2 - NIFTY intraday options: three-strategy backtest")
    canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(GRID)
    canvas.line(MARGIN, 13 * mm, PAGE_W - MARGIN, 13 * mm)
    canvas.restoreState()


def build(out: Path) -> None:
    """Assemble the cover, Part 1 (analysis) and Part 2 (report) and write the PDF."""
    analysis = (RESULTS / "ANALYSIS.md").read_text(encoding="utf-8")
    report = (RESULTS / "report.md").read_text(encoding="utf-8")
    # drop the report's trailing pointer to ANALYSIS.md: the analysis is Part 1 of this same PDF
    report = re.split(r"^## Analysis\s*$", report, flags=re.M)[0]

    story = [Spacer(1, 30 * mm),
             Paragraph("NIFTY Intraday Options", S["title"]),
             Paragraph("Mean Reversion, Directional and Semi-Directional strategies, and their combined "
                       "portfolio", S["part"]),
             Paragraph(f"Generated {datetime.now():%Y-%m-%d %H:%M} from <font name='{MONO}'>results/ANALYSIS.md"
                       f"</font> and <font name='{MONO}'>results/report.md</font>.", S["cover_note"]),
             Spacer(1, 6),
             Paragraph("<b>Part 1 - Analysis:</b> the written interpretation: headline results, where the profit "
                       "comes from, each strategy's rules, strengths and weaknesses, the before/after changes "
                       "made for robustness, and the risks.", S["cover_note"]),
             Paragraph("<b>Part 2 - Backtest report:</b> the full generated output: setup, equity curves, "
                       "performance statistics, first vs second half, correlations, robustness grid, "
                       "before/after comparisons, and breakdowns by exit reason, days to expiry, direction "
                       "and month.", S["cover_note"]),
             Spacer(1, 10),
             Paragraph("<b>Development and test:</b> rules and sizes were selected on 2 Jan 2023 - 29 Sep 2025 "
                       "(in-sample) and tested on 30 Sep 2025 - 25 Sep 2026. See the note at the start of Part 1.",
                       S["cover_note"]),
             PageBreak(),
             Paragraph("Part 1 - Analysis", S["part"]),
             Paragraph(inline(first_heading(analysis)), S["h1"])]
    story += md_to_flowables(analysis, RESULTS, skip_title=True)
    story += [PageBreak(), Paragraph("Part 2 - Backtest report", S["part"]),
              Paragraph(inline(first_heading(report)), S["h1"])]
    story += md_to_flowables(report, RESULTS, skip_title=True)

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=18 * mm,
                            title="NIFTY Intraday Options - Three-Strategy Backtest", author="Quant Camp Q2")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"wrote {out}")


def build_docs(inputs: list[Path], out: Path) -> None:
    """One PDF from any Markdown file(s), in order: the first file's title is set large at the top,
    each later file starts on a new page, and images are resolved relative to each file."""
    story, title = [], ""
    for n, path in enumerate(inputs):
        md = path.read_text(encoding="utf-8")
        heading = first_heading(md)
        title = title or heading
        if n:
            story.append(PageBreak())
        story.append(Paragraph(inline(heading), S["title"] if n == 0 else S["h1"]))
        story += md_to_flowables(md, path.parent, skip_title=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=18 * mm, title=title, author="Quant Camp Q2")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", type=Path,
                    help="Markdown file(s) to convert; omit to build results/Q2_Report.pdf as before")
    ap.add_argument("--out", type=Path, help="output PDF (default: results/Q2_Report.pdf, or the first input as .pdf)")
    a = ap.parse_args()
    if a.inputs:
        build_docs(a.inputs, a.out or a.inputs[0].with_suffix(".pdf"))
    else:
        build(a.out or RESULTS / "Q2_Report.pdf")
