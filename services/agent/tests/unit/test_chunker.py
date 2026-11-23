"""Heading-aware chunker and document parsers (BS-07, LLD-8, FR-RG-02)."""

import io
import json
import re
from pathlib import Path

import pytest

from app.ingest.chunker import MAX_TOKENS, chunk_markdown, estimate_tokens, parse_blocks, slugify
from app.ingest.parsers import UnsupportedDocument, parse_document

pytestmark = pytest.mark.req("FR-RG-02")
KB = Path(__file__).resolve().parents[4] / "mock-data" / "data" / "kb"
DOCS = json.loads((KB / "documents.json").read_text())
ERRORS = json.loads((KB.parent / "catalog" / "error_codes.json").read_text())


def chunks_for(doc):
    return chunk_markdown((KB.parent / doc["path"]).read_text(), doc)


def tables(md):
    return [b.text for b in parse_blocks(md) if b.kind == "table"]


# ---------- acceptance on the seed corpus ----------

def test_no_table_is_split_across_chunks():
    for doc in DOCS:
        md = (KB.parent / doc["path"]).read_text()
        cs = chunks_for(doc)
        for t in tables(md):
            assert sum(t in c.content for c in cs) == 1, (doc["doc_id"], t[:60])


def test_each_manual_error_code_section_is_exactly_one_chunk():
    for doc in (d for d in DOCS if d["doc_type"] == "manual"):
        md = (KB.parent / doc["path"]).read_text()
        headings = re.findall(r"^### ([A-Z]{1,3}\d{1,3}) — ", md, flags=re.MULTILINE)
        coded = [c for c in chunks_for(doc) if c.metadata["error_codes"]]
        assert [c.metadata["error_codes"] for c in coded] == [[f"{doc['family']}-{h}"] for h in headings]
        for c in coded:   # the whole section, with every numbered cause, is in the chunk
            code = c.metadata["error_codes"][0].split("-", 1)[1]
            section = md.split(f"### {code} — ", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
            assert section.split("\n", 1)[1].strip() == c.content


def test_error_code_chunks_cover_the_catalog():
    coded = {code for d in DOCS for c in chunks_for(d) for code in c.metadata["error_codes"]}
    assert coded == {e["id"] for e in ERRORS}


def test_heading_path_anchor_and_metadata():
    dhd = next(d for d in DOCS if d["doc_id"] == "DOC-MAN-DHD")
    cs = chunks_for(dhd)
    e04 = next(c for c in cs if c.metadata["error_codes"] == ["DHD-E04"])
    assert e04.heading_path == ["Dehumidifying dryer — operation and troubleshooting manual (demo)",
                                "Error codes and troubleshooting", "E04 — Dew point high (display: `DEW PT HIGH`)"]
    assert e04.anchor == "e04-dew-point-high"
    assert e04.metadata | {"models": []} == {"doc_id": "DOC-MAN-DHD", "version": "1.0", "doc_type": "manual",
                                             "family": "DHD", "models": [], "error_codes": ["DHD-E04"],
                                             "status": "active"}
    assert e04.embed_text.startswith("Dehumidifying dryer") and "MT-DHD-ROTOR" in e04.content
    assert [c.ord for c in cs] == list(range(len(cs)))
    for doc in DOCS:
        anchors = [c.anchor for c in chunks_for(doc)]
        assert len(anchors) == len(set(anchors)), doc["doc_id"]


def test_models_metadata_uses_known_models_and_superseded_status():
    dhd = next(d for d in DOCS if d["doc_id"] == "DOC-MAN-DHD")
    models = next(c for c in chunk_markdown((KB.parent / dhd["path"]).read_text(), dhd,
                                            known_models={"DHD-50", "DHD-150"}) if c.anchor == "models")
    assert models.metadata["models"] == ["DHD-150", "DHD-50"]
    v1 = next(d for d in DOCS if d["doc_id"] == "DOC-POL-WARRANTY-V1")
    assert {c.metadata["status"] for c in chunks_for(v1)} == {"superseded"}


def test_chunk_sizes_respect_max_on_the_corpus():
    for doc in DOCS:
        for c in chunks_for(doc):
            assert c.token_count <= MAX_TOKENS, (doc["doc_id"], c.anchor)


def test_chunking_is_deterministic():
    doc = DOCS[0]
    assert chunks_for(doc) == chunks_for(doc)


# ---------- rules on synthetic input ----------

def para(n, words=60):
    return " ".join(f"word{n}_{i}" for i in range(words))


def test_long_section_is_packed_without_splitting_blocks():
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| row{i} | {'x' * 40} |" for i in range(60))  # > max alone
    items = "\n".join(f"- item {i}: " + para(i, 30) for i in range(12))
    md = "# Guide\n\n## Long\n\n" + "\n\n".join(para(i) for i in range(10)) + "\n\n" + table + "\n\n" + items
    cs = chunk_markdown(md, {"doc_id": "D"})
    assert len(cs) > 2 and all(c.heading_path == ["Guide", "Long"] for c in cs)
    assert [c.anchor for c in cs] == [f"long-part-{i + 1}" for i in range(len(cs))]
    assert sum(table in c.content for c in cs) == 1   # oversized table stays whole
    for c in cs:
        if table not in c.content:
            assert c.token_count <= MAX_TOKENS
    for i in range(12):   # each list item lands in exactly one chunk, whole
        item = f"- item {i}: " + para(i, 30)
        assert sum(item in c.content for c in cs) == 1


def test_oversized_error_code_section_stays_one_chunk():
    body = "\n\n".join(f"{i}. **Cause {i}** " + para(i, 80) for i in range(1, 12))
    cs = chunk_markdown(f"# Manual\n\n## Error codes\n\n### E09 — Huge fault\n\n{body}\n", {"family": "DHD"})
    coded = [c for c in cs if c.metadata["error_codes"]]
    assert len(coded) == 1 and coded[0].token_count > MAX_TOKENS and coded[0].metadata["error_codes"] == ["DHD-E09"]


def test_service_bulletin_error_code_heading():
    md = (KB.parents[1] / "upload-samples" / "SB-2026-12_DHD_rotor_revision.md").read_text()
    cs = chunk_markdown(md, {"family": "DHD"}, known_models={"DHD-100", "DHD-150"})
    e11 = [c for c in cs if c.metadata["error_codes"]]
    assert [c.metadata["error_codes"] for c in e11] == [["DHD-E11"]] and "MT-DHD-RMOTOR" in e11[0].content
    assert next(c for c in cs if c.anchor == "rotor-revision").metadata["error_codes"] == []


def test_h4_stays_inside_section_and_preamble_is_kept():
    cs = chunk_markdown("# Title\n\nIntro text.\n\n## A\n\nalpha\n\n#### Detail\n\nbeta\n\n### B\n\ngamma", {})
    assert [(c.heading_path, c.content) for c in cs] == [
        (["Title"], "Intro text."), (["Title", "A"], "alpha\n\n**Detail**\n\nbeta"), (["Title", "A", "B"], "gamma")]


def test_code_fences_and_nested_lists_are_single_blocks():
    md = "## S\n\n```\n| not a table |\n# not a heading\n```\n\n- a\n  - a1\n  - a2\n- b"
    kinds = [(b.kind, b.text) for b in parse_blocks(md)]
    assert kinds[1][0] == "code" and "# not a heading" in kinds[1][1]
    assert [t for k, t in kinds if k == "list_item"] == ["- a\n  - a1\n  - a2", "- b"]


def test_helpers():
    assert slugify("E04 — Dew point high (display: `DEW PT HIGH`)") == "e04-dew-point-high"
    assert slugify("**What** is covered?") == "what-is-covered"
    assert estimate_tokens("") == 0 and estimate_tokens("abcd" * 10) == 10


# ---------- parsers ----------

def test_parse_markdown_and_text_passthrough():
    assert parse_document("a.md", b"# T\n\nbody") == "# T\n\nbody"
    assert parse_document("a.TXT", "﻿plain".encode()) == "plain"
    with pytest.raises(UnsupportedDocument):
        parse_document("a.xlsx", b"")


def test_parse_docx_keeps_headings_lists_and_tables():
    import docx

    d = docx.Document()
    d.add_heading("Service bulletin", level=1)
    d.add_heading("New code E11 — Rotor stall", level=3)
    d.add_paragraph("Belt slipping", style="List Number")
    d.add_paragraph("Motor failed", style="List Number")
    t = d.add_table(rows=2, cols=2)
    for (r, c), v in {(0, 0): "Part", (0, 1): "Price", (1, 0): "MT-DHD-RBELT", (1, 1): "1450"}.items():
        t.cell(r, c).text = v
    buf = io.BytesIO()
    d.save(buf)
    md = parse_document("sb.docx", buf.getvalue())
    assert md.startswith("# Service bulletin\n\n### New code E11 — Rotor stall\n\n1. Belt slipping\n1. Motor failed")
    assert "| Part | Price |\n|---|---|\n| MT-DHD-RBELT | 1450 |" in md
    cs = chunk_markdown(md, {"family": "DHD"})
    assert cs[-1].metadata["error_codes"] == ["DHD-E11"] and "| MT-DHD-RBELT | 1450 |" in cs[-1].content


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> "
            b"/Contents 4 0 R >>"),
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_parse_pdf_extracts_text():
    assert "Rotor drive motor stall" in parse_document("sb.pdf", _pdf("Rotor drive motor stall"))
