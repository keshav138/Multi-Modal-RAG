"""
chunk_text.py — turn manual PDFs into text chunks, saved as a JSONL file.

For every page it does four things:
  1. Finds tables and turns each one into its own markdown chunk.
  2. Reads the remaining text blocks (paragraphs, headings) in human reading
     order. Manuals are two-column, so "left column first, then right".
  3. Packs those blocks into chunks of roughly MAX_CHARS characters.
  4. Saves each chunk with source file, page number and position (bbox),
     so answers can cite "manual X, page Y" later.

Usage:
    python chunk_text.py manual1.pdf manuals_dir/ --out data/out/text_chunks.jsonl
"""

import argparse
import json
import re
from pathlib import Path

import pymupdf

# ---- settings you can tweak ---------------------------------------------
MAX_CHARS = 1200       # target chunk size (~300 tokens)
MIN_CHARS = 80         # a last chunk smaller than this is glued onto the one before
FULL_WIDTH = 0.6       # a block wider than 60% of the page spans both columns
TABLE_OVERLAP = 0.6    # a text block 60%+ inside a table's area belongs to the table
OVERLAP_MAX_CHARS = 300  # only repeat the previous chunk's last piece if it is this short


# ---- step 1: tables -----------------------------------------------------
def find_tables(page):
    """Return a list of {"rect", "text"} where text is the table as markdown."""
    tables = []
    for t in page.find_tables().tables:
        if t.row_count < 2 or t.col_count < 2:
            continue  # ignore false alarms (a single ruled line is not a table)
        markdown = t.to_markdown().strip()
        if markdown:
            tables.append({"rect": pymupdf.Rect(t.bbox), "text": markdown})
    return tables


# ---- step 2: text blocks in reading order -------------------------------
def clean_text(text):
    """Turn PDF line-wrapped text into one tidy line."""
    text = re.sub(r"-\n(?=[a-z])", "", text)  # "clear-\nance" -> "clearance"
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_inside_a_table(rect, tables):
    """True if most of this block's area lies within any table."""
    for table in tables:
        overlap = rect & table["rect"]  # the shared area of the two rectangles
        if not overlap.is_empty and overlap.get_area() / rect.get_area() >= TABLE_OVERLAP:
            return True
    return False


def get_text_blocks(page, tables):
    """All text blocks on the page, except the ones that are really table cells."""
    blocks = []
    # each raw block is (x0, y0, x1, y1, text, block_number, block_type)
    for x0, y0, x1, y1, text, _, block_type in page.get_text("blocks"):
        if block_type != 0:  # 0 = text, 1 = image
            continue
        text = clean_text(text)
        rect = pymupdf.Rect(x0, y0, x1, y1)
        if text and not is_inside_a_table(rect, tables):
            blocks.append({"rect": rect, "text": text})
    return blocks


def sort_in_reading_order(blocks, page_width):
    """
    Sort blocks the way a person reads a two-column page.

    A plain top-to-bottom sort would jump between columns line by line.
    Instead each block gets a sort key of (section, column, y):
      - section: how many full-width blocks (titles, wide figures) sit above it.
                 Everything between two full-width blocks is one section.
      - column:  0 = left, 1 = right, 2 = the full-width block itself
                 (it comes after the columns that sit above it).
      - y:       top-to-bottom within the same column.
    """
    middle = page_width / 2
    for b in blocks:
        b["full_width"] = b["rect"].width > FULL_WIDTH * page_width
    full_width_blocks = [b for b in blocks if b["full_width"]]

    def sort_key(b):
        section = sum(1 for f in full_width_blocks
                      if f is not b and f["rect"].y1 <= b["rect"].y0)
        if b["full_width"]:
            column = 2
        elif (b["rect"].x0 + b["rect"].x1) / 2 < middle:
            column = 0
        else:
            column = 1
        return (section, column, b["rect"].y0)

    return sorted(blocks, key=sort_key)


# ---- step 3: pack blocks into chunks ------------------------------------
def split_long_text(text):
    """Split a very long paragraph at sentence ends so no piece exceeds MAX_CHARS."""
    if len(text) <= MAX_CHARS:
        return [text]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) + 1 > MAX_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def pack_into_chunks(pieces):
    """Group consecutive pieces until a group would exceed MAX_CHARS."""
    chunks, current, size = [], [], 0
    for piece in pieces:
        if current and size + len(piece["text"]) > MAX_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(piece)
        size += len(piece["text"]) + 1
    if current:
        chunks.append(current)

    # a tiny leftover chunk is useless on its own, so glue it onto the previous one
    if len(chunks) > 1 and sum(len(p["text"]) for p in chunks[-1]) < MIN_CHARS:
        last = chunks.pop()
        chunks[-1] += last
    return chunks


# ---- step 4: build the records ------------------------------------------
def process_pdf(pdf_path):
    records = []
    doc = pymupdf.open(pdf_path)

    for page_number, page in enumerate(doc, start=1):
        tables = find_tables(page)
        blocks = get_text_blocks(page, tables)
        blocks = sort_in_reading_order(blocks, page.rect.width)

        # break over-long paragraphs into smaller pieces, each keeping its position
        pieces = [{"rect": b["rect"], "text": t}
                  for b in blocks for t in split_long_text(b["text"])]
        chunks = pack_into_chunks(pieces)

        entries = []  # (content_type, text, rect) for everything on this page
        for n, group in enumerate(chunks):
            text = " ".join(p["text"] for p in group)
            if n > 0:
                # overlap: repeat the last piece of the previous chunk as context,
                # so an idea split across two chunks is still found by either one.
                # (skipped when that piece is long, or chunks would nearly double in size)
                previous_piece = chunks[n - 1][-1]["text"]
                if len(previous_piece) <= OVERLAP_MAX_CHARS:
                    text = previous_piece + " " + text
            rect = group[0]["rect"]
            for p in group[1:]:
                rect = rect | p["rect"]  # smallest rectangle covering both
            entries.append(("text", text, rect))
        for table in tables:
            entries.append(("table", table["text"], table["rect"]))

        for n, (content_type, text, rect) in enumerate(entries):
            records.append({
                "chunk_id": f"{pdf_path.stem}_p{page_number}_c{n}",
                "modality": "text",
                "content_type": content_type,   # "text" or "table"
                "source": pdf_path.name,
                "page": page_number,
                "bbox": [round(v, 1) for v in rect],
                "text": text,
            })

    doc.close()
    return records


def collect_pdfs(paths):
    """Expand a mix of PDF files and folders into a list of PDF files."""
    pdfs = []
    for path in paths:
        if path.is_dir():
            pdfs += sorted(path.glob("*.pdf"))
        elif path.suffix.lower() == ".pdf":
            pdfs.append(path)
    return list(dict.fromkeys(pdfs))  # remove duplicates, keep order


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+", help="PDF files and/or folders of PDFs")
    parser.add_argument("--out", type=Path, default=Path("data/out/text_chunks.jsonl"))
    args = parser.parse_args()

    pdfs = collect_pdfs(args.inputs)
    if not pdfs:
        parser.error("No PDFs found in the given inputs")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(args.out, "w") as f:
        for pdf in pdfs:
            records = process_pdf(pdf)
            for record in records:
                f.write(json.dumps(record) + "\n")
            total += len(records)
            print(f"{pdf.name}: {len(records)} chunks")
    print(f"Total: {total} chunks -> {args.out}")


if __name__ == "__main__":
    main()
