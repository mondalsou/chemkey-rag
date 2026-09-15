"""Inventory embedded PDF images and apply crude heuristic labels.

Phase 1 of figure/table OCR: metadata only. Does not write image binaries,
does not call PubChem, and does not touch the search index.

    python scripts/inventory_pdf_images.py
    python scripts/inventory_pdf_images.py --papers papers --out data/image_inventory.json

Classification is heuristic (no models). Labels:

- table_image: very wide aspect plus ruled/grid-like rows, or thin strips
  (MDPI v2 of paper A stores many table rules as ~1601x5 rasters)
- structure_drawing: near-square sparse line art
- scheme_or_reaction: other line-art composites, often wide
- other: logos, photos, dense colour
- unknown: no dimensions and pixels could not be decoded

Confidence is always "heuristic". This path is not wired into ingest; a future
optional flag will gate OCR / DECIMER / MolScribe (see docs/FIGURE_TABLE_OCR.md).
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import math
import os
import sys
from collections import Counter

from pypdf import PdfReader

try:
    from PIL import Image
except ImportError:  # pragma: no cover - streamlit/matplotlib already pull this in
    Image = None


LABELS = (
    "structure_drawing",
    "table_image",
    "scheme_or_reaction",
    "other",
    "unknown",
)

REQUIRED_IMAGE_FIELDS = (
    "paper_id",
    "filename",
    "page",
    "image_index",
    "width",
    "height",
    "byte_size",
    "label",
    "confidence",
)

REQUIRED_SUMMARY_FIELDS = (
    "total_images",
    "counts_by_paper",
    "counts_by_class",
    "images",
)

DEFAULT_PAPERS_DIR = "papers"
DEFAULT_OUT = "data/image_inventory.json"

# Geometry that is already decisive; skip pixel decode.
_STRIP_MAX_THICKNESS = 16
_STRIP_MIN_ASPECT = 8.0


def find_pdfs(papers_dir=DEFAULT_PAPERS_DIR):
    """Return sorted PDF paths. Missing folder or empty glob -> []."""
    return sorted(glob.glob(os.path.join(papers_dir, "*.pdf")))


def paper_id_from_filename(filename):
    """A_paracetamol_cocrystals.pdf -> A; otherwise the stem."""
    stem, _ext = os.path.splitext(os.path.basename(filename))
    if "_" in stem:
        prefix, _rest = stem.split("_", 1)
        if prefix:
            return prefix
    return stem or filename


def _as_int(value, default=None):
    if value is None:
        return default
    if hasattr(value, "get_object"):
        try:
            value = value.get_object()
        except Exception:
            return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_xobject(page, key):
    """Follow a page.images key (name or nested Form path) to the Image XObject."""
    names = list(key) if isinstance(key, (list, tuple)) else [key]
    resources = page.get("/Resources")
    obj = None
    for i, name in enumerate(names):
        if resources is None:
            return None
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        xobjects = resources.get("/XObject") if hasattr(resources, "get") else None
        if xobjects is None:
            return None
        if hasattr(xobjects, "get_object"):
            xobjects = xobjects.get_object()
        obj = xobjects.get(name) if hasattr(xobjects, "get") else None
        if obj is None:
            return None
        if hasattr(obj, "get_object"):
            obj = obj.get_object()
        if i < len(names) - 1:
            resources = obj.get("/Resources") if hasattr(obj, "get") else None
    return obj


def _filter_name(xobj):
    if xobj is None:
        return None
    filt = xobj.get("/Filter") if hasattr(xobj, "get") else None
    if filt is None:
        return None
    if hasattr(filt, "get_object"):
        filt = filt.get_object()
    if isinstance(filt, list):
        return [str(part) for part in filt]
    return str(filt)


def _embedded_byte_size(xobj, encoded_bytes=b""):
    size = _as_int(xobj.get("/Length") if xobj is not None and hasattr(xobj, "get") else None)
    if size:
        return size
    if encoded_bytes:
        return len(encoded_bytes)
    if xobj is not None and hasattr(xobj, "get_data"):
        try:
            return len(xobj.get_data())
        except Exception:
            return 0
    return 0


def _is_image_mask(xobj):
    if xobj is None or not hasattr(xobj, "get"):
        return False
    flag = xobj.get("/ImageMask")
    if flag is None:
        return False
    if hasattr(flag, "get_object"):
        flag = flag.get_object()
    return bool(flag)


def _downsample_gray(pil_image, max_width=240):
    gray = pil_image.convert("L")
    if gray.width > max_width:
        height = max(1, round(gray.height * max_width / gray.width))
        gray = gray.resize((max_width, height), Image.Resampling.BILINEAR)
    return gray


def pixel_features(pil_image):
    """Cheap raster stats for the heuristic classifier. No ML."""
    gray = _downsample_gray(pil_image)
    width, height = gray.size
    pixels = list(gray.tobytes())
    n = len(pixels) or 1
    ink = sum(1 for v in pixels if v < 200)
    white = sum(1 for v in pixels if v > 240)
    # 16-bin entropy of grayscale
    bins = [0] * 16
    for v in pixels:
        bins[min(v * 16 // 256, 15)] += 1
    entropy = 0.0
    for count in bins:
        if count:
            p = count / n
            entropy -= p * math.log2(p)

    row_means = []
    row_stds = []
    for y in range(height):
        row = pixels[y * width : (y + 1) * width]
        mean = sum(row) / width
        var = sum((v - mean) ** 2 for v in row) / width
        row_means.append(mean)
        row_stds.append(var ** 0.5)

    hline_idx = []
    for y in range(height):
        delta = abs(row_means[y] - row_means[y - 1]) if y else 0.0
        if row_stds[y] < 25.0 and delta > 8.0:
            hline_idx.append(y)
    grid = False
    if len(hline_idx) >= 4:
        gaps = [b - a for a, b in zip(hline_idx, hline_idx[1:]) if b > a]
        if gaps:
            gmean = sum(gaps) / len(gaps)
            gvar = sum((g - gmean) ** 2 for g in gaps) / len(gaps)
            if gvar ** 0.5 < max(2.0, 0.35 * gmean):
                grid = True

    rgb = pil_image.convert("RGB")
    if rgb.width > 240:
        rgb = rgb.resize(
            (240, max(1, round(rgb.height * 240 / rgb.width))),
            Image.Resampling.BILINEAR,
        )
    raw = rgb.tobytes()
    rgb_px = list(zip(raw[0::3], raw[1::3], raw[2::3])) if raw else []
    chroma = 0.0
    if rgb_px:
        chroma = sum(abs(r - g) + abs(g - b) for r, g, b in rgb_px) / (2 * len(rgb_px))

    return {
        "ink_frac": ink / n,
        "white_frac": white / n,
        "entropy": entropy,
        "n_hlines": len(hline_idx),
        "hline_frac": len(hline_idx) / height if height else 0.0,
        "grid": grid,
        "chroma": chroma,
    }


def classify_embedded_image(width, height, pil_image=None):
    """Return (label, reasons). Imperfect by design; confidence is heuristic."""
    if (not width or not height) and pil_image is not None:
        width, height = pil_image.size
    if not width or not height:
        return "unknown", ["missing_dimensions"]

    aspect = width / height

    if height <= _STRIP_MAX_THICKNESS and aspect >= _STRIP_MIN_ASPECT:
        return "table_image", ["thin_wide_strip"]
    if width <= _STRIP_MAX_THICKNESS and aspect <= 1.0 / _STRIP_MIN_ASPECT:
        return "table_image", ["thin_tall_strip"]

    if pil_image is None:
        if aspect >= 2.2:
            return "scheme_or_reaction", ["wide_aspect_only"]
        if 0.65 <= aspect <= 1.45:
            return "structure_drawing", ["near_square_only"]
        return "other", ["geometry_only"]

    feat = pixel_features(pil_image)
    line_art = (
        feat["white_frac"] >= 0.45
        and feat["ink_frac"] <= 0.35
        and feat["chroma"] < 20
    )
    # Tables in this corpus are pale with ruled rows, not dense publisher banners.
    table_like = (
        aspect >= 1.7
        and feat["n_hlines"] >= 4
        and feat["white_frac"] >= 0.45
        and feat["ink_frac"] <= 0.50
    ) or (
        feat["grid"]
        and aspect >= 1.35
        and feat["white_frac"] >= 0.40
        and feat["ink_frac"] <= 0.50
    )
    if table_like:
        reasons = ["horizontal_lines"]
        if feat["grid"]:
            reasons.insert(0, "grid_like")
        if aspect >= 1.7:
            reasons.insert(0, "wide_aspect")
        return "table_image", reasons

    if 0.55 <= aspect <= 1.7 and line_art:
        return "structure_drawing", ["near_square", "sparse_line_art"]

    if line_art:
        return "scheme_or_reaction", ["line_art_composite"]

    if aspect >= 1.6 and feat["white_frac"] >= 0.35:
        return "scheme_or_reaction", ["wide_composite"]

    return "other", ["dense_or_photo"]


def _pil_from_xobject(xobj):
    """Decode without going through page.images when geometry needs pixels."""
    if Image is None or xobj is None or not hasattr(xobj, "get_data"):
        return None
    try:
        data = xobj.get_data()
    except Exception:
        return None
    filt = str(_filter_name(xobj) or "")
    if "DCTDecode" in filt or "DCT" in filt:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            return img.convert("RGB")
        except Exception:
            pass
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.convert("RGB")
    except Exception:
        pass
    width = _as_int(xobj.get("/Width"))
    height = _as_int(xobj.get("/Height"))
    if not width or not height:
        return None
    colorspace = str(xobj.get("/ColorSpace") or "")
    try:
        if "Gray" in colorspace and len(data) >= width * height:
            return Image.frombytes("L", (width, height), data[: width * height])
        if len(data) >= width * height * 3:
            return Image.frombytes("RGB", (width, height), data[: width * height * 3])
    except Exception:
        return None
    return None


def _needs_pixels(width, height):
    if not width or not height:
        return True
    aspect = width / height
    if height <= _STRIP_MAX_THICKNESS and aspect >= _STRIP_MIN_ASPECT:
        return False
    if width <= _STRIP_MAX_THICKNESS and aspect <= 1.0 / _STRIP_MIN_ASPECT:
        return False
    return True


def inventory_page_images(page, filename, page_number):
    """List every embedded image on one page (Form-nested and top-level)."""
    records = []
    paper_id = paper_id_from_filename(filename)
    try:
        keys = list(page.images.keys())
    except Exception:
        keys = []

    for image_index, key in enumerate(keys):
        xobj = _resolve_xobject(page, key)
        width = _as_int(xobj.get("/Width") if xobj is not None else None)
        height = _as_int(xobj.get("/Height") if xobj is not None else None)
        byte_size = _embedded_byte_size(xobj)
        name = (
            " ".join(str(part) for part in key)
            if isinstance(key, (list, tuple))
            else str(key)
        )

        pil = None
        decode_error = None
        if _needs_pixels(width, height):
            try:
                image_file = page.images[key]
            except Exception as exc:
                image_file = None
                decode_error = type(exc).__name__
            else:
                if not byte_size:
                    byte_size = len(image_file.data or b"")
                pil = image_file.image
                if pil is None:
                    pil = _pil_from_xobject(xobj)
                if (not width or not height) and pil is not None:
                    width, height = pil.size
            if pil is None and decode_error is None:
                pil = _pil_from_xobject(xobj)

        if _is_image_mask(xobj) and pil is None and not width:
            label, reasons = "other", ["image_mask"]
        else:
            label, reasons = classify_embedded_image(width, height, pil)
            if label == "unknown" and decode_error:
                reasons = [f"decode_{decode_error}"]

        records.append(
            {
                "paper_id": paper_id,
                "filename": filename,
                "page": page_number,
                "image_index": image_index,
                "width": width,
                "height": height,
                "byte_size": byte_size,
                "label": label,
                "confidence": "heuristic",
                "reasons": reasons,
                "xobject_name": name,
                "filter": _filter_name(xobj),
            }
        )
        if pil is not None:
            try:
                pil.close()
            except Exception:
                pass

    return records


def inventory_pdf(pdf_path):
    filename = os.path.basename(pdf_path)
    reader = PdfReader(pdf_path)
    records = []
    for page_number, page in enumerate(reader.pages, start=1):
        records.extend(inventory_page_images(page, filename, page_number))
    return records


def build_inventory(pdf_paths, papers_dir=DEFAULT_PAPERS_DIR):
    images = []
    for path in pdf_paths:
        images.extend(inventory_pdf(path))

    by_paper = Counter()
    by_class = Counter()
    by_paper_class = {}
    for rec in images:
        by_paper[rec["filename"]] += 1
        by_class[rec["label"]] += 1
        slot = by_paper_class.setdefault(rec["filename"], Counter())
        slot[rec["label"]] += 1

    counts_by_class = {label: int(by_class.get(label, 0)) for label in LABELS}
    counts_by_paper = {
        name: {
            "total": int(by_paper[name]),
            "by_class": {label: int(by_paper_class[name].get(label, 0)) for label in LABELS},
        }
        for name in sorted(by_paper)
    }

    return {
        "generated_by": "scripts/inventory_pdf_images.py",
        "classifier": "heuristic",
        "papers_dir": papers_dir,
        "pdf_count": len(pdf_paths),
        "pdfs": [os.path.basename(p) for p in pdf_paths],
        "total_images": len(images),
        "counts_by_paper": counts_by_paper,
        "counts_by_class": counts_by_class,
        "images": images,
        "notes": (
            "Heuristic labels only. No OCR, no DECIMER/MolScribe, no index writes. "
            "Does not store page text (paper C must not leak into a public file)."
        ),
    }


def print_summary(summary, file=sys.stdout):
    print("Image inventory (heuristic; does not update the search index)", file=file)
    print(f"  PDFs scanned: {summary['pdf_count']}", file=file)
    print(f"  total embedded images: {summary['total_images']}", file=file)
    print(file=file)
    print("  by paper:", file=file)
    if not summary["counts_by_paper"]:
        print("    (none)", file=file)
    for name, info in summary["counts_by_paper"].items():
        print(f"    {name:42} {info['total']:5d}", file=file)
    print(file=file)
    print("  by class:", file=file)
    for label in LABELS:
        print(f"    {label:22} {summary['counts_by_class'].get(label, 0):5d}", file=file)


def write_inventory(summary, out_path):
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inventory embedded images in papers/*.pdf (no index writes)."
    )
    parser.add_argument("--papers", default=DEFAULT_PAPERS_DIR, help="folder of PDFs")
    parser.add_argument("--out", default=DEFAULT_OUT, help="JSON output path (gitignored)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the summary only; do not write JSON",
    )
    args = parser.parse_args(argv)

    pdf_paths = find_pdfs(args.papers)
    if not pdf_paths:
        print(
            f"SKIP: no PDFs in {args.papers}/. See papers/PAPERS.md. "
            "Nothing written; search index unchanged.",
            file=sys.stderr,
        )
        return 0

    print(f"Scanning {len(pdf_paths)} PDF(s) under {args.papers}/")
    summary = build_inventory(pdf_paths, papers_dir=args.papers)
    print_summary(summary)
    if not args.dry_run:
        write_inventory(summary, args.out)
        print(f"\nWrote {args.out} ({summary['total_images']} images)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
