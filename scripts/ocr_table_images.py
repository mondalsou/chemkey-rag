"""Phase 2: layout OCR for image-only tables. Native pypdf text tables are untouched.

    python scripts/ocr_table_images.py
    python scripts/ocr_table_images.py --inventory data/image_inventory.json

Takes inventory rows labeled table_image. SKIPS MDPI v2 thin table-rule rasters
(~1601x5). Runs Tesseract only on remaining table-like rasters — never on
structure drawings. REFUSES when mean word confidence is below
CONFIDENCE_THRESHOLD or a row/column grid cannot be recovered. Never invents
cell values. Column edges are voted on by row, so a full-width caption or a
centred header cannot collapse the table into a single column.

Writes gitignored data/table_ocr.json. Does not mutate data/index.json.
Optional ingest flag --image-tables on build_index.py is OFF by default and
only runs this sidecar; it does not replace page.extract_text().

Tesseract / pytesseract are optional (see requirements-table-ocr.txt).
DECIMER / MolScribe are phase 3 and are not imported here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory_pdf_images as inv

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PAPERS_DIR = "papers"
DEFAULT_INVENTORY = "data/image_inventory.json"
DEFAULT_OUT = "data/table_ocr.json"
DEFAULT_CROPS_DIR = "data/extracted_tables"

# Tesseract word confidence is 0–100. Below this mean we refuse the table.
# 70 is conservative: the paper-B figure rasters in this corpus sit in the
# 30–55 range; a COSMO-RS plot that scraped 65.7 was a false accept at 65.
CONFIDENCE_THRESHOLD = 70.0
MIN_ROWS = 2
MIN_COLS = 2
MIN_FILL_FRACTION = 0.40
MIN_OCR_WIDTH = 120
MIN_OCR_HEIGHT = 40
TESSERACT_PSM = 6  # uniform block of text; layout comes from word boxes
WORD_LEVEL = 5

# Column recovery. Scales are multiples of the median word height, i.e. of the
# body font size, so they hold at any raster DPI.
COLUMN_GAP_SCALE = 1.5     # a gutter is a within-row gap this much wider than a line
COLUMN_MERGE_SCALE = 1.0   # gutters this close in x are the same column edge
COLUMN_SUPPORT_FRACTION = 1.0 / 3.0  # rows that must agree before an edge is real


def tesseract_available():
    """True only if pytesseract *and* a tesseract binary respond."""
    try:
        import pytesseract
    except ImportError:
        return False
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def tesseract_version_string():
    if not tesseract_available():
        return None
    import pytesseract
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception:
        return None


def skip_reason_for_record(rec):
    """Return a skip reason, or None if this row is an OCR candidate.

    Only table_image rows are considered. Thin strips are table RULES, not
    tables. Tiny leftovers (logos that slipped the inventory heuristic) are
    also skipped. structure_drawing / scheme_or_reaction are never OCR'd.
    """
    if rec.get("label") != "table_image":
        return "not_table_image"
    width = rec.get("width") or 0
    height = rec.get("height") or 0
    if inv.is_thin_table_rule(width, height):
        return "thin_table_rule"
    if width < MIN_OCR_WIDTH or height < MIN_OCR_HEIGHT:
        return "not_table_like"
    return None


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _cluster_1d(items, coord_fn, gap):
    if not items:
        return []
    ordered = sorted(items, key=coord_fn)
    groups = [[ordered[0]]]
    for item in ordered[1:]:
        prev = groups[-1][-1]
        if coord_fn(item) - coord_fn(prev) <= gap:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def words_from_tesseract_data(data):
    """Keep Tesseract level-5 tokens with conf >= 0. Empty / -1 conf dropped."""
    words = []
    n = len(data.get("text") or [])
    for i in range(n):
        try:
            level = int(data["level"][i])
        except (KeyError, ValueError, TypeError):
            continue
        if level != WORD_LEVEL:
            continue
        try:
            conf = float(data["conf"][i])
        except (KeyError, ValueError, TypeError):
            continue
        if conf < 0:
            continue
        text = str(data["text"][i] or "").strip()
        if not text:
            continue
        words.append(
            {
                "text": text,
                "conf": conf,
                "left": int(data["left"][i]),
                "top": int(data["top"][i]),
                "width": int(data["width"][i]),
                "height": int(data["height"][i]),
            }
        )
    return words


def mean_word_confidence(words):
    if not words:
        return None
    return sum(w["conf"] for w in words) / len(words)


def raw_text_from_words(words):
    if not words:
        return ""
    rows = _cluster_1d(
        words,
        lambda w: w["top"] + w["height"] / 2.0,
        gap=max(8.0, _median([w["height"] for w in words]) * 0.7),
    )
    lines = []
    for row in rows:
        row_sorted = sorted(row, key=lambda w: w["left"])
        lines.append(" ".join(w["text"] for w in row_sorted))
    return "\n".join(lines)


def _row_gap_midpoints(row, min_gap):
    """Midpoints of the within-row horizontal gaps wide enough to be gutters."""
    ordered = sorted(row, key=lambda w: w["left"])
    right = ordered[0]["left"] + ordered[0]["width"]
    mids = []
    for word in ordered[1:]:
        if word["left"] - right >= min_gap:
            mids.append((right + word["left"]) / 2.0)
        right = max(right, word["left"] + word["width"])
    return mids


def column_boundaries(row_groups, min_gap, tolerance, min_support):
    """x positions where enough rows independently show a gutter.

    Voting per row, rather than projecting the whole raster onto x, is what
    makes this survive a table whose caption or centred header straddles the
    columns: those rows simply cast no vote the data rows agree with. A single
    chain of overlapping word boxes used to collapse every column into one.
    """
    votes = [
        {"x": mid, "row": index}
        for index, row in enumerate(row_groups)
        for mid in _row_gap_midpoints(row, min_gap)
    ]
    boundaries = []
    for cluster in _cluster_1d(votes, lambda v: v["x"], tolerance):
        if len({v["row"] for v in cluster}) >= min_support:
            boundaries.append(_median([v["x"] for v in cluster]))
    return boundaries


def _column_of(word, boundaries):
    center = word["left"] + word["width"] / 2.0
    return sum(1 for edge in boundaries if center > edge)


def _is_spanning_row(row, boundaries, min_gap):
    """True when a row proposes no column edge of its own yet crosses one.

    A caption, or a wrapped continuation of one, runs across the table at
    ordinary word spacing: it never shows a gutter, so it must not be cut at
    edges only the data rows voted for.
    """
    if _row_gap_midpoints(row, min_gap):
        return False
    left = min(w["left"] for w in row)
    right = max(w["left"] + w["width"] for w in row)
    return any(left < edge < right for edge in boundaries)


def recover_grid(words, min_rows=MIN_ROWS, min_cols=MIN_COLS):
    """Cluster word boxes into a rectangular grid, or None.

    Empty cells stay empty strings. Never fills a cell that had no OCR token.
    A row spanning every column edge is kept whole in its first cell rather
    than chopped at edges it does not obey.
    """
    if not words:
        return None
    line_height = _median([w["height"] for w in words])
    row_groups = _cluster_1d(
        words,
        lambda w: w["top"] + w["height"] / 2.0,
        max(8.0, line_height * 0.7),
    )
    n_rows = len(row_groups)
    if n_rows < min_rows:
        return None
    min_gap = max(12.0, line_height * COLUMN_GAP_SCALE)
    boundaries = column_boundaries(
        row_groups,
        min_gap=min_gap,
        tolerance=max(8.0, line_height * COLUMN_MERGE_SCALE),
        min_support=max(2, math.ceil(n_rows * COLUMN_SUPPORT_FRACTION)),
    )
    n_cols = len(boundaries) + 1
    if n_cols < min_cols:
        return None

    cells = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    spanning_rows = []
    for r, row in enumerate(row_groups):
        ordered = sorted(row, key=lambda w: w["left"])
        if _is_spanning_row(row, boundaries, min_gap):
            spanning_rows.append(r)
            cells[r][0] = " ".join(w["text"] for w in ordered)
            continue
        buckets = [[] for _ in range(n_cols)]
        for word in ordered:
            buckets[_column_of(word, boundaries)].append(word["text"])
        for c, pieces in enumerate(buckets):
            cells[r][c] = " ".join(pieces)

    # Fill is measured over the tabular rows only: a caption legitimately
    # occupies one cell, so counting its blanks would punish real tables.
    tabular = [r for r in range(n_rows) if r not in spanning_rows]
    total = len(tabular) * n_cols
    filled = sum(
        1 for r in tabular for value in cells[r] if value.strip()
    )
    return {
        "n_rows": n_rows,
        "n_cols": n_cols,
        "cells": cells,
        "fill_fraction": filled / total if total else 0.0,
        "column_boundaries": [round(edge, 1) for edge in boundaries],
        "spanning_rows": spanning_rows,
    }


def _refused(reason, mean_conf=None, raw_text=None):
    return {
        "status": "refused",
        "reason": reason,
        "mean_confidence": mean_conf,
        "raw_text": raw_text,
        "cells": None,
        "n_rows": None,
        "n_cols": None,
        "column_boundaries": None,
        "spanning_rows": None,
    }


def decide_ocr_result(words, confidence_threshold=CONFIDENCE_THRESHOLD):
    """Single choke point: accept a grid, or refuse. Never invents cells."""
    mean_conf = mean_word_confidence(words)
    raw = raw_text_from_words(words)
    if not words:
        return _refused("no_text", mean_conf, "")
    if mean_conf is None or mean_conf < confidence_threshold:
        return _refused("low_confidence", mean_conf, raw)
    grid = recover_grid(words)
    if grid is None:
        return _refused("grid_not_recovered", mean_conf, raw)
    if grid["fill_fraction"] < MIN_FILL_FRACTION:
        return _refused("sparse_grid", mean_conf, raw)
    return {
        "status": "accepted",
        "reason": None,
        "mean_confidence": mean_conf,
        "raw_text": raw,
        "cells": grid["cells"],
        "n_rows": grid["n_rows"],
        "n_cols": grid["n_cols"],
        "column_boundaries": grid["column_boundaries"],
        "spanning_rows": grid["spanning_rows"],
    }


def _prepare_for_ocr(pil_image):
    gray = pil_image.convert("L")
    if gray.height < 400:
        scale = max(2, round(400 / max(gray.height, 1)))
        gray = gray.resize((gray.width * scale, gray.height * scale))
    return gray.convert("RGB")


def ocr_image_to_data(pil_image):
    """Call Tesseract. Isolated so checks can mock this."""
    import pytesseract
    from pytesseract import Output

    prepared = _prepare_for_ocr(pil_image)
    return pytesseract.image_to_data(
        prepared,
        output_type=Output.DICT,
        config=f"--psm {TESSERACT_PSM}",
        lang="eng",
    )


def load_or_build_inventory(papers_dir, inventory_path):
    if inventory_path and os.path.exists(inventory_path):
        with open(inventory_path, encoding="utf-8") as fh:
            return json.load(fh)
    pdfs = inv.find_pdfs(papers_dir)
    if not pdfs:
        return None
    return inv.build_inventory(pdfs, papers_dir=papers_dir)


def _pdf_path_for(filename, papers_dir):
    return os.path.join(papers_dir, filename)


def stable_table_id(filename, page, image_index):
    raw = f"{filename}|{page}|{image_index}".encode()
    return "tbl_" + hashlib.sha1(raw).hexdigest()[:14]


def _save_crop(image, crops_dir, table_id):
    """Retain the raster that was OCR'd, so a reviewer can check the cells."""
    path = Path(crops_dir) / f"{table_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="PNG", optimize=True)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def ocr_table_record(rec, papers_dir, ocr_fn=ocr_image_to_data, crops_dir=None):
    """Skip, refuse, or accept one inventory row. Raster stays in memory."""
    base = {
        "id": stable_table_id(
            rec.get("filename"), rec.get("page"), rec.get("image_index")
        ),
        "paper_id": rec.get("paper_id"),
        "filename": rec.get("filename"),
        "page": rec.get("page"),
        "image_index": rec.get("image_index"),
        "width": rec.get("width"),
        "height": rec.get("height"),
        "xobject_name": rec.get("xobject_name"),
        "image_path": None,
    }
    reason = skip_reason_for_record(rec)
    if reason:
        return {**base, **_refused(reason), "status": "skipped"}
    pdf_path = _pdf_path_for(rec.get("filename") or "", papers_dir)
    pil = None
    if os.path.exists(pdf_path):
        pil = inv.extract_embedded_raster(
            pdf_path, rec["page"], rec["image_index"]
        )
    if pil is None:
        return {**base, **_refused("decode_failed")}
    if crops_dir:
        base["image_path"] = _save_crop(pil, crops_dir, base["id"])
    try:
        data = ocr_fn(pil)
        words = words_from_tesseract_data(data)
        decided = decide_ocr_result(words)
    except Exception as exc:
        decided = _refused(f"ocr_error:{type(exc).__name__}")
    finally:
        try:
            pil.close()
        except Exception:
            pass
    return {**base, **decided}


def build_table_ocr(
    inventory, papers_dir, ocr_fn=ocr_image_to_data, crops_dir=DEFAULT_CROPS_DIR
):
    rows = []
    for rec in inventory.get("images") or []:
        if rec.get("label") != "table_image":
            continue
        rows.append(
            ocr_table_record(rec, papers_dir, ocr_fn=ocr_fn, crops_dir=crops_dir)
        )

    counts = Counter(r["status"] for r in rows)
    skip_reasons = Counter(
        r["reason"] for r in rows if r["status"] == "skipped"
    )
    refuse_reasons = Counter(
        r["reason"] for r in rows if r["status"] == "refused"
    )
    return {
        "generated_by": "scripts/ocr_table_images.py",
        "classifier": "tesseract_layout",
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "min_rows": MIN_ROWS,
        "min_cols": MIN_COLS,
        "min_fill_fraction": MIN_FILL_FRACTION,
        "column_gap_scale": COLUMN_GAP_SCALE,
        "column_support_fraction": COLUMN_SUPPORT_FRACTION,
        "tesseract_psm": TESSERACT_PSM,
        "tesseract_version": tesseract_version_string(),
        "papers_dir": papers_dir,
        "crops_dir": crops_dir,
        "counts": {
            "table_image_candidates": len(rows),
            "skipped": int(counts.get("skipped", 0)),
            "skipped_thin_table_rule": int(skip_reasons.get("thin_table_rule", 0)),
            "skipped_not_table_like": int(skip_reasons.get("not_table_like", 0)),
            "ocr_attempted": int(counts.get("accepted", 0) + counts.get("refused", 0)),
            "accepted": int(counts.get("accepted", 0)),
            "refused": int(counts.get("refused", 0)),
            "refused_by_reason": dict(refuse_reasons),
        },
        "tables": rows,
        "notes": (
            "Image-table OCR sidecar. Native pypdf page.extract_text() is unchanged. "
            "Thin MDPI v2 table-rule strips are skipped. Low Tesseract confidence or "
            "an unrecovered grid is a refusal, not a guessed table. No page text from "
            "paper C is written to a committed file. Structure drawings are not OCR'd. "
            "Not wired into default ingest."
        ),
    }


def print_summary(summary, file=sys.stdout):
    counts = summary["counts"]
    print("Image-table OCR (sidecar; search index unchanged)", file=file)
    print(f"  table_image candidates: {counts['table_image_candidates']}", file=file)
    print(f"  skipped thin table rules: {counts['skipped_thin_table_rule']}", file=file)
    print(f"  skipped not table-like: {counts['skipped_not_table_like']}", file=file)
    print(f"  OCR attempted: {counts['ocr_attempted']}", file=file)
    print(f"  accepted grids: {counts['accepted']}", file=file)
    print(f"  refused: {counts['refused']}", file=file)
    if counts.get("refused_by_reason"):
        for reason, n in sorted(counts["refused_by_reason"].items()):
            print(f"    {reason}: {n}", file=file)
    print(
        f"  confidence threshold: {summary['confidence_threshold']} "
        f"(Tesseract 0–100, psm {summary['tesseract_psm']})",
        file=file,
    )


def write_table_ocr(summary, out_path):
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")


def run_table_ocr(
    papers_dir=DEFAULT_PAPERS_DIR,
    inventory_path=DEFAULT_INVENTORY,
    out_path=DEFAULT_OUT,
    dry_run=False,
    ocr_fn=ocr_image_to_data,
    crops_dir=DEFAULT_CROPS_DIR,
):
    inventory = load_or_build_inventory(papers_dir, inventory_path)
    if inventory is None:
        return None
    summary = build_table_ocr(
        inventory, papers_dir, ocr_fn=ocr_fn, crops_dir=crops_dir
    )
    if not dry_run:
        write_table_ocr(summary, out_path)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="OCR image-only tables from the PDF inventory (no index writes)."
    )
    parser.add_argument("--papers", default=DEFAULT_PAPERS_DIR)
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    pdfs = inv.find_pdfs(args.papers)
    if not pdfs and not os.path.exists(args.inventory):
        print(
            f"SKIP: no PDFs in {args.papers}/ and no {args.inventory}. "
            "Nothing written; search index unchanged.",
            file=sys.stderr,
        )
        return 0
    if not tesseract_available():
        print(
            "SKIP: Tesseract/pytesseract not available. "
            "Install tesseract-ocr and pip install -r requirements-table-ocr.txt. "
            "Search index unchanged.",
            file=sys.stderr,
        )
        return 0

    print(f"Image-table OCR (threshold {CONFIDENCE_THRESHOLD}, psm {TESSERACT_PSM})")
    summary = run_table_ocr(
        papers_dir=args.papers,
        inventory_path=args.inventory,
        out_path=args.out,
        dry_run=args.dry_run,
    )
    if summary is None:
        print("SKIP: no inventory and no PDFs.", file=sys.stderr)
        return 0
    print_summary(summary)
    if not args.dry_run:
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
