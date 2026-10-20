"""
extract_figures.py

Extracts individual figures (diagrams/photos + their in-figure labels and
captions) from technical PDF manuals, without dragging in surrounding body
text. Works at the region level, not the page level: page-wide "rasterize if
shape count > threshold" pulls in whole columns of text; this clusters
vector/raster primitives into distinct figure bounding boxes first, then
crops just those regions.

Usage:
    python extract_figures.py input.pdf output_dir/
    python extract_figures.py manual1.pdf manual2.pdf manual3.pdf output_dir/
    python extract_figures.py manuals_dir/ output_dir/   # processes every *.pdf inside

Output:
    output_dir/<pdf_stem>_p<page>_fig<n>.png   — cropped figure image, per source PDF
    output_dir/all_figures_metadata.json       — one combined record per figure across
                                                  all PDFs processed in this run:
        {page, bbox, caption, source_pdf, image_path}
"""

import argparse
import json
import re
from pathlib import Path

import pymupdf

# ---- tunables -----------------------------------------------------------
MERGE_DIST_PT = 14          # merge primitives/clusters within this many points
CAPTION_MAX_GAP_PT = 40     # a caption may start up to this far below its figure
MIN_CLUSTER_AREA_PT2 = 2500 # drop tiny clusters (bullets, rules, noise) — ~50x50pt
MIN_PRIMITIVES = 3          # drop clusters built from too few paths (stray lines)
RENDER_ZOOM = 3.0           # 3x zoom ≈ 216 DPI from a 72 DPI base page
CROP_PADDING_PT = 4
CAPTION_PATTERN = re.compile(r"^\s*(fig(ure)?s?\.?\s*\d+)", re.IGNORECASE)


def union_bbox(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def expanded(bbox, pad):
    return (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)


def bboxes_close(a, b, dist):
    """True if bbox a and b overlap or are within `dist` points of each other."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    gap_x = max(bx0 - ax1, ax0 - bx1, 0)
    gap_y = max(by0 - ay1, ay0 - by1, 0)
    return gap_x <= dist and gap_y <= dist


def bbox_contains(outer, inner, tolerance=2):
    """True if inner bbox sits within outer bbox (with small tolerance)."""
    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner
    return (
        ix0 >= ox0 - tolerance
        and iy0 >= oy0 - tolerance
        and ix1 <= ox1 + tolerance
        and iy1 <= oy1 + tolerance
    )


def cluster_primitives(primitives, dist):
    """
    Greedy union-find style merge: repeatedly merge any two clusters whose
    bboxes are within `dist` of each other until no more merges happen.
    `primitives` is a list of dicts with a 'bbox' key and a 'count' key.
    Returns a list of cluster dicts: {bbox, count}.
    """
    clusters = [{"bbox": p["bbox"], "count": p.get("count", 1)} for p in primitives]

    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            if clusters[i] is None:
                continue
            for j in range(i + 1, len(clusters)):
                if clusters[j] is None:
                    continue
                if bboxes_close(clusters[i]["bbox"], clusters[j]["bbox"], dist):
                    clusters[i]["bbox"] = union_bbox(clusters[i]["bbox"], clusters[j]["bbox"])
                    clusters[i]["count"] += clusters[j]["count"]
                    clusters[j] = None
                    merged = True
        clusters = [c for c in clusters if c is not None]

    return clusters


def get_page_primitives(page):
    """Collect bboxes for vector drawing paths and embedded raster images."""
    primitives = []

    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.width <= 0 or rect.height <= 0:
            continue
        primitives.append({"bbox": (rect.x0, rect.y0, rect.x1, rect.y1), "count": 1})

    for img in page.get_image_info():
        bbox = img["bbox"]
        primitives.append({"bbox": bbox, "count": 3})  # weight raster images higher

    return primitives


def get_text_lines(page):
    """
    Every line of text on the page as {"bbox", "text"}.
    Lines, not blocks: PDF "blocks" can glue a figure code like "A07588" onto
    the caption line below it, which would hide the "Fig." at the start.
    """
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # 0 = text block
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                lines.append({"bbox": tuple(line["bbox"]), "text": text})
    return lines


def find_caption(cluster_bbox, text_lines):
    """
    Find the "Fig. N ..." line that belongs to this figure. It must:
      - overlap the figure horizontally (sit under it, not beside it),
      - start in the figure's lower half or below it (so it can also be found
        if the figure's bbox swallowed it), and
      - be at most CAPTION_MAX_GAP_PT below the figure.
    If several qualify, the closest one wins.
    Returns (caption_text, caption_bbox) or (None, None).
    """
    cx0, cy0, cx1, cy1 = cluster_bbox
    lower_half_start = (cy0 + cy1) / 2
    best, best_distance = None, None
    for line in text_lines:
        if not CAPTION_PATTERN.match(line["text"]):
            continue
        x0, y0, x1, y1 = line["bbox"]
        if x1 < cx0 or x0 > cx1:
            continue
        if y0 < lower_half_start or y0 - cy1 > CAPTION_MAX_GAP_PT:
            continue
        distance = abs(y0 - cy1)
        if best is None or distance < best_distance:
            best, best_distance = line, distance
    if best:
        return best["text"], best["bbox"]
    return None, None


def process_page(page, page_num, pdf_stem, out_dir, doc_name, debug=False):
    primitives = get_page_primitives(page)
    if not primitives:
        return []

    clusters = cluster_primitives(primitives, MERGE_DIST_PT)
    clusters = [
        c for c in clusters
        if c["count"] >= MIN_PRIMITIVES
        and (c["bbox"][2] - c["bbox"][0]) * (c["bbox"][3] - c["bbox"][1]) >= MIN_CLUSTER_AREA_PT2
    ]
    if not clusters:
        return []

    text_lines = get_text_lines(page)
    if debug:
        caption_lines = [line for line in text_lines if CAPTION_PATTERN.match(line["text"])]
        print(f"  page {page_num}: {len(clusters)} figure clusters, "
              f"{len(caption_lines)} caption-like lines")
        for c in clusters:
            print("    cluster", [round(v) for v in c["bbox"]])
        for line in caption_lines:
            print("    caption line", [round(v) for v in line["bbox"]], repr(line["text"][:60]))
    records = []

    for i, cluster in enumerate(clusters, start=1):
        bbox = cluster["bbox"]

        # pull in a caption below the figure, expanding the crop to include it
        caption_text, caption_bbox = find_caption(bbox, text_lines)
        final_bbox = union_bbox(bbox, caption_bbox) if caption_bbox else bbox
        final_bbox = expanded(final_bbox, CROP_PADDING_PT)

        # sanity clip to page bounds
        final_bbox = (
            max(final_bbox[0], 0),
            max(final_bbox[1], 0),
            min(final_bbox[2], page.rect.width),
            min(final_bbox[3], page.rect.height),
        )

        clip_rect = pymupdf.Rect(*final_bbox)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM), clip=clip_rect)

        img_name = f"{pdf_stem}_p{page_num}_fig{i}.png"
        img_path = out_dir / img_name
        pix.save(img_path)

        records.append({
            "source_pdf": doc_name,
            "page": page_num,
            "bbox": final_bbox,
            "caption": caption_text,
            "image_path": str(img_path),
        })

    return records


def resolve_pdf_paths(inputs):
    """Expand a mix of PDF file paths and directories into a flat, deduped list of PDFs."""
    pdf_paths = []
    for p in inputs:
        if p.is_dir():
            pdf_paths.extend(sorted(p.glob("*.pdf")))
        elif p.suffix.lower() == ".pdf":
            pdf_paths.append(p)
        else:
            print(f"Skipping non-PDF input: {p}")
    # dedupe while preserving order
    seen = set()
    unique = []
    for p in pdf_paths:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(p)
    return unique


def process_pdf(pdf_path, output_dir, debug=False):
    doc = pymupdf.open(pdf_path)
    pdf_stem = pdf_path.stem
    records = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        records.extend(
            process_page(page, page_num + 1, pdf_stem, output_dir, pdf_path.name, debug)
        )
    num_pages = len(doc)  # read this before closing the document
    doc.close()
    return records, num_pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        type=Path,
        nargs="+",
        help="One or more PDF files and/or directories containing PDFs, "
             "followed by the output directory",
    )
    parser.add_argument("--debug", action="store_true",
                        help="print the figure clusters and caption-like lines found on each page")
    args = parser.parse_args()

    if len(args.inputs) < 2:
        parser.error("Provide at least one PDF/directory plus an output directory")

    *pdf_inputs, output_dir = args.inputs
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths = resolve_pdf_paths(pdf_inputs)
    if not pdf_paths:
        parser.error("No PDF files found in the given inputs")

    all_records = []
    for pdf_path in pdf_paths:
        records, num_pages = process_pdf(pdf_path, output_dir, args.debug)
        all_records.extend(records)
        print(f"{pdf_path.name}: extracted {len(records)} figures from {num_pages} pages")

    metadata_path = output_dir / "all_figures_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(all_records, f, indent=2)

    print(f"\nTotal: {len(all_records)} figures from {len(pdf_paths)} PDF(s) -> {output_dir}")
    print(f"Combined metadata written to {metadata_path}")


if __name__ == "__main__":
    main()
