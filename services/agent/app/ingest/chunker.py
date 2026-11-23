"""Heading-aware Markdown chunker (LLD-8, FR-RG-02).

Rules: split at H1/H2/H3; never split a table, list item or code block; each manual error-code section
(`### E04 — Dew point high ...`, or an H2/H3 "... code E11 — ..." in bulletins) is exactly one chunk; target ~350 tokens, max 700 (a single oversized block or
error-code section stays whole); overlap 0. Each chunk stores heading_path, a unique anchor (slug of its heading)
and metadata {doc_id, version, doc_type, family, models[], error_codes[], status}.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any

TARGET_TOKENS = 350
MAX_TOKENS = 700

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
# "E04 — Dew point high" or "New error code E11 — Rotor stall" (service bulletins)
_ERROR_HEADING = re.compile(r"(?:^|\bcode\s+)([A-Z]{1,3}\d{1,3})\s*(?:—|–|-{1,2}|:)\s+\S", re.IGNORECASE)
_MODEL = re.compile(r"\b[A-Z]{2,4}-[A-Z]?\d+[A-Z0-9]*\b")


def estimate_tokens(text: str) -> int:
    """Cheap, provider-independent estimate (~4 characters per token)."""
    return max(1, math.ceil(len(text) / 4)) if text.strip() else 0


def slugify(heading: str) -> str:
    text = re.sub(r"\s*\((?:display|see)\b.*\)\s*$", "", heading, flags=re.IGNORECASE)  # drop "(display: `X`)"
    text = re.sub(r"[`*_]", "", text).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "section"


@dataclass
class Block:
    kind: str  # heading | table | list_item | code | paragraph
    text: str
    level: int = 0  # heading level


@dataclass
class Chunk:
    ord: int
    heading_path: list[str]
    anchor: str
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def embed_text(self) -> str:
        """What gets embedded: heading context + content (the tsv uses heading_path + content too)."""
        return " > ".join(self.heading_path) + "\n\n" + self.content if self.heading_path else self.content


def parse_blocks(markdown: str) -> list[Block]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _FENCE.match(line):
            fence = _FENCE.match(line).group(1)  # type: ignore[union-attr]
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            blocks.append(Block("code", "\n".join(lines[i:j + 1])))
            i = j + 1
            continue
        if m := _HEADING.match(line):
            blocks.append(Block("heading", m.group(2).strip(), level=len(m.group(1))))
            i += 1
            continue
        if line.lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            blocks.append(Block("table", "\n".join(lines[i:j])))
            i = j
            continue
        if m := _LIST_ITEM.match(line):
            indent = len(m.group(1))
            j = i + 1
            # continuation: indented deeper (nested items, wrapped text) until a blank line or a sibling item
            while j < len(lines) and lines[j].strip() and not _HEADING.match(lines[j]):
                nm = _LIST_ITEM.match(lines[j])
                if nm and len(nm.group(1)) <= indent:
                    break
                if not nm and not lines[j].startswith(" " * (indent + 1)) and not lines[j].startswith("\t"):
                    break
                j += 1
            blocks.append(Block("list_item", "\n".join(lines[i:j])))
            i = j
            continue
        j = i + 1
        while (j < len(lines) and lines[j].strip() and not _HEADING.match(lines[j])
               and not lines[j].lstrip().startswith("|") and not _LIST_ITEM.match(lines[j])
               and not _FENCE.match(lines[j])):
            j += 1
        blocks.append(Block("paragraph", "\n".join(lines[i:j])))
        i = j
    return blocks


def _join(blocks: list[Block]) -> str:
    out: list[str] = []
    for k, b in enumerate(blocks):
        # consecutive list items of one list stay tight; everything else is separated by a blank line
        sep = "\n" if k and b.kind == "list_item" and blocks[k - 1].kind == "list_item" else "\n\n"
        out.append((sep if k else "") + b.text)
    return "".join(out).strip()


def _pack(blocks: list[Block]) -> list[list[Block]]:
    """Greedy packing of whole blocks into parts of about TARGET_TOKENS (never above MAX unless one block is)."""
    parts: list[list[Block]] = []
    cur: list[Block] = []
    for b in blocks:
        t = estimate_tokens(b.text)
        size = estimate_tokens(_join(cur))
        if cur and (size + t > MAX_TOKENS or (size >= TARGET_TOKENS and size + t > TARGET_TOKENS)):
            parts.append(cur)
            cur = []
        cur.append(b)
    if cur:
        parts.append(cur)
    return parts


def chunk_markdown(markdown: str, meta: dict[str, Any] | None = None,
                   known_models: set[str] | None = None) -> list[Chunk]:
    meta = dict(meta or {})
    family = meta.get("family")
    sections: list[tuple[list[str], int, list[Block]]] = []  # (heading_path, level, body blocks)
    heads: dict[int, str] = {}
    path: list[str] = []
    body: list[Block] = []
    level = 0

    def flush() -> None:
        if body:
            sections.append((list(path), level, list(body)))

    for b in parse_blocks(markdown):
        if b.kind == "heading" and b.level <= 3:
            flush()
            body = []
            heads[b.level] = b.text
            for deeper in [k for k in heads if k > b.level]:
                del heads[deeper]
            path = [heads[k] for k in sorted(heads)]
            level = b.level
        elif b.kind == "heading":  # H4+ stays inside its section, kept as bold text
            body.append(Block("paragraph", f"**{b.text}**"))
        else:
            body.append(b)
    flush()

    chunks: list[Chunk] = []
    anchors: dict[str, int] = {}
    for hpath, lvl, blocks in sections:
        heading = hpath[-1] if hpath else meta.get("title", "")
        err = _ERROR_HEADING.search(heading) if lvl in (2, 3) else None
        parts = [blocks] if err else _pack(blocks)
        for n, part in enumerate(parts):
            content = _join(part)
            base = slugify(heading) + (f"-part-{n + 1}" if len(parts) > 1 else "")
            anchors[base] = anchors.get(base, 0) + 1
            anchor = base if anchors[base] == 1 else f"{base}-{anchors[base]}"
            text = " ".join(hpath) + " " + content
            models = sorted({m for m in _MODEL.findall(text) if known_models is None or m in known_models})
            codes = []
            if err:
                code = err.group(1)
                codes = [f"{family}-{code}" if family else code]
            chunks.append(Chunk(
                ord=len(chunks), heading_path=hpath, anchor=anchor, content=content,
                token_count=estimate_tokens(content),
                metadata={"doc_id": meta.get("doc_id"), "version": meta.get("version"),
                          "doc_type": meta.get("doc_type"), "family": family, "models": models,
                          "error_codes": codes, "status": meta.get("status", "active")}))
    return chunks
