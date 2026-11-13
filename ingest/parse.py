"""Stage 1: circular PDFs -> chunks.jsonl. Slow (table detection); run once.

Text via PyMuPDF, tables via pdfplumber serialised to markdown. Chunks never cross a
page boundary, so page_number is exact. 1024 chars / 128 overlap (benchmark-chosen).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import pdfplumber
import pymupdf

from app.config import settings

log = logging.getLogger("ingest.parse")

CHUNK_SIZE = 1024
CHUNK_OVERLAP = 128

CIRCULAR_NO = re.compile(r"NPCI\s*/\s*(\d{4}-\d{2})\s*/\s*([A-Za-z&]+)\s*/\s*(\d+[A-Za-z]?)")
CIRCULAR_NO_PRODUCT = re.compile(r"NPCI\s*/\s*(RuPay|UPI|IMPS|NETC)\s*/\s*([A-Za-z]+)\s*/\s*(\d+[A-Za-z]?)", re.I)
FY_IN_NAME = re.compile(r"FY[-_ ]*(?:20)?(\d{2})[-_ ](?:20)?(\d{2})", re.I)
OC_IN_NAME = re.compile(r"OC[-_ ]*(?:No[_.]*)?[-_ ]*(\d+[A-Z]?)", re.I)


def detect_product(filename: str, first_pages: str) -> str:
    name = filename.upper()
    for key, product in (("RU_PAY", "RUPAY"), ("RUPAY", "RUPAY"), ("UPI", "UPI"), ("IMPS", "IMPS"),
                         ("NETC", "NETC"), ("FAS", "NETC")):
        if key in name:
            return product
    counts = {p: len(re.findall(p, first_pages, re.I)) for p in ("RuPay", "UPI", "IMPS", "NETC")}
    best = max(counts, key=counts.get)
    return best.upper() if counts[best] >= 3 else "OTHER"


def detect_circular_meta(filename: str, first_pages: str) -> tuple[str | None, str | None]:
    m = CIRCULAR_NO.search(first_pages)
    if m:
        return f"NPCI/{m.group(1)}/{m.group(2).upper()}/{m.group(3)}", m.group(1)
    fy = FY_IN_NAME.search(filename)
    fiscal = f"20{fy.group(1)}-{fy.group(2)}" if fy else None
    m = CIRCULAR_NO_PRODUCT.search(first_pages)
    if m:
        return f"NPCI/{m.group(1)}/{m.group(2).upper()}/{m.group(3)}", fiscal
    oc = OC_IN_NAME.search(filename)
    return (f"OC {oc.group(1)}" if oc else None), fiscal


def split(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()  # blank lines would eat the 1024-char budget
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + CHUNK_SIZE - CHUNK_OVERLAP, end)  # avoid splitting a word
            end = cut if cut > start else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [c for c in chunks if c]


def table_to_markdown(table: list[list[str | None]]) -> str:
    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in table if row and any(row)]
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def parse_pdf(path: Path) -> list[dict]:
    out: list[dict] = []
    with pymupdf.open(path) as doc, pdfplumber.open(path) as plumb:
        first = "\n".join(doc[i].get_text("text") for i in range(min(2, doc.page_count)))
        product = detect_product(path.name, "\n".join(p.get_text("text") for p in doc))
        circular_no, fiscal_year = detect_circular_meta(path.name, first)
        for pno in range(doc.page_count):
            pieces: list[tuple[str, str]] = [("text", c) for c in split(doc[pno].get_text("text"))]
            try:
                for t in plumb.pages[pno].extract_tables():
                    md = table_to_markdown(t)
                    pieces += [("table", c) for c in split(md)] if len(md) > CHUNK_SIZE else ([("table", md)] if md else [])
            except Exception as e:  # one bad table must not drop the page's text
                log.warning("table extraction failed %s p%d: %s", path.name, pno + 1, e)
            for idx, (ctype, content) in enumerate(pieces):
                out.append({
                    "chunk_id": hashlib.sha1(f"{path.name}|{pno + 1}|{idx}".encode()).hexdigest()[:20],
                    "source_filename": path.name, "page_number": pno + 1, "product": product,
                    "circular_no": circular_no, "fiscal_year": fiscal_year, "content_type": ctype,
                    "chunk_index": idx, "content": content,
                })
    return out


def run() -> Path:
    src, dest = Path(settings.CIRCULARS_DIR), Path(settings.CHUNKS_PATH)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(src.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"INGEST ERROR: no PDFs found in {src}")
    total = 0
    with dest.open("w", encoding="utf-8") as f:
        for p in pdfs:
            try:
                chunks = parse_pdf(p)
            except Exception as e:  # a malformed file is a data problem, not a reason to stop the batch
                log.error("skipping %s: %s", p.name, e)
                continue
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
            total += len(chunks)
            log.info("%-60s product=%-6s chunks=%d", p.name[:60], chunks[0]["product"] if chunks else "-", len(chunks))
    log.info("wrote %d chunks to %s", total, dest)
    return dest
