"""Document parsers → Markdown (LLD-8). MD/TXT are native; DOCX keeps headings, lists and tables;
PDF yields text per page (PDFs carry no reliable structure, so chunks fall back to size-based packing)."""

import io
import re
from pathlib import PurePath

SUPPORTED = {".md", ".markdown", ".txt", ".pdf", ".docx"}


class UnsupportedDocument(ValueError):
    pass


def parse_document(filename: str, data: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedDocument(f"unsupported file type {ext or '(none)'}; supported: {', '.join(sorted(SUPPORTED))}")
    if ext in (".md", ".markdown", ".txt"):
        return data.decode("utf-8-sig", errors="replace")
    if ext == ".pdf":
        return _pdf(data)
    return _docx(data)


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    return "\n\n".join(re.sub(r"[ \t]+\n", "\n", t) for t in pages if t)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _docx(data: bytes) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(io.BytesIO(data))
    out: list[str] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if m := re.match(r"Heading (\d)", style):
                out.append("#" * min(int(m.group(1)), 6) + " " + text)
            elif style == "Title":
                out.append("# " + text)
            elif "List Number" in style:
                out.append("1. " + text)
            elif "List" in style:
                out.append("- " + text)
            else:
                out.append(text)
        elif tag == "tbl":
            rows = [[_cell(c.text) for c in r.cells] for r in Table(child, doc).rows]
            if rows:
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                out.append("\n".join(lines))
    # blank line between blocks, but keep consecutive list items together
    md: list[str] = []
    for k, block in enumerate(out):
        tight = k and re.match(r"(-|1\.) ", block) and re.match(r"(-|1\.) ", out[k - 1])
        md.append(("\n" if tight else "\n\n") + block if k else block)
    return "".join(md) + "\n"
