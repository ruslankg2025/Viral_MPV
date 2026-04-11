import docx
import sys
from docx.oxml.ns import qn

doc = docx.Document(r"D:/PROGRAMS/VIRAL_MPV/ТЗ_ВИРАЛ-монитор.docx")

def iter_block_items(parent):
    from docx.document import Document as _Doc
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    if isinstance(parent, _Doc):
        parent_elm = parent.element.body
    else:
        parent_elm = parent._element
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)

def numpr_info(p):
    pPr = p._p.find(qn('w:pPr'))
    if pPr is None:
        return None
    numPr = pPr.find(qn('w:numPr'))
    if numPr is None:
        return None
    ilvl = numPr.find(qn('w:ilvl'))
    numId = numPr.find(qn('w:numId'))
    return (
        int(ilvl.get(qn('w:val'))) if ilvl is not None else 0,
        int(numId.get(qn('w:val'))) if numId is not None else 0,
    )

lines = []
for block in iter_block_items(doc):
    if isinstance(block, docx.text.paragraph.Paragraph):
        p = block
        text = p.text
        style = (p.style.name if p.style else "") or ""
        num = numpr_info(p)
        if not text.strip() and num is None:
            lines.append("")
            continue
        if style.startswith("Heading"):
            try:
                level = int(style.split()[-1])
            except Exception:
                level = 2
            lines.append("#" * level + " " + text.strip())
        elif style.lower().startswith("title"):
            lines.append("# " + text.strip())
        elif num is not None:
            ilvl, numId = num
            indent = "  " * ilvl
            lines.append(f"{indent}- {text.strip()}")
        else:
            lines.append(text)
    else:
        # table
        tbl = block
        rows = tbl.rows
        if not rows:
            continue
        header = [c.text.strip().replace("\n", " ") for c in rows[0].cells]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")
        for r in rows[1:]:
            cells = [c.text.strip().replace("\n", " ").replace("|", "\\|") for c in r.cells]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

with open(r"D:/PROGRAMS/VIRAL_MPV/ТЗ_ВИРАЛ-монитор.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("OK", len(lines), "lines")
