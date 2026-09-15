"""Run: python checks/check_image_inventory.py

No network. Does not mutate data/index.json. Clone-friendly: if papers/*.pdf
are absent, heuristic tests still run and the corpus scan is SKIP/OK.
"""
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inventory_pdf_images as inv

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "papers"
INDEX_PATHS = (ROOT / "data" / "index.json", ROOT / "data" / "index.public.json")


def _index_fingerprints():
    out = {}
    for path in INDEX_PATHS:
        if path.exists():
            stat = path.stat()
            out[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return out


def _table_image():
    im = Image.new("L", (400, 90), 255)
    draw = ImageDraw.Draw(im)
    for y in range(8, 90, 8):
        draw.line((4, y, 396, y), fill=0, width=2)
    return im


def _structure_image():
    im = Image.new("L", (200, 210), 255)
    draw = ImageDraw.Draw(im)
    draw.ellipse((50, 55, 150, 155), outline=0, width=2)
    draw.line((100, 55, 100, 20), fill=0, width=2)
    draw.line((150, 105, 180, 105), fill=0, width=2)
    return im


def _other_image():
    im = Image.new("RGB", (220, 80), (30, 90, 160))
    return im


before = _index_fingerprints()

label, reasons = inv.classify_embedded_image(1601, 5, None)
assert label == "table_image" and "thin_wide_strip" in reasons

label, reasons = inv.classify_embedded_image(400, 90, _table_image())
assert label == "table_image", (label, reasons)
assert "horizontal_lines" in reasons or "grid_like" in reasons

label, reasons = inv.classify_embedded_image(200, 210, _structure_image())
assert label == "structure_drawing", (label, reasons)
assert "sparse_line_art" in reasons

label, reasons = inv.classify_embedded_image(220, 80, _other_image())
assert label == "other", (label, reasons)

label, reasons = inv.classify_embedded_image(None, None, None)
assert label == "unknown" and "missing_dimensions" in reasons

assert inv.paper_id_from_filename("A_paracetamol_cocrystals.pdf") == "A"
assert inv.paper_id_from_filename("notes.pdf") == "notes"

pdfs = inv.find_pdfs(str(PAPERS))
if not pdfs:
    assert before == _index_fingerprints()
    print("SKIP/OK: no papers/*.pdf; clone-friendly. Heuristic classifier checks passed.")
    sys.exit(0)

summary = inv.build_inventory(pdfs, papers_dir=str(PAPERS))
for field in inv.REQUIRED_SUMMARY_FIELDS:
    assert field in summary, field
assert summary["total_images"] > 0, "PDFs present but inventory found no images"
assert isinstance(summary["counts_by_paper"], dict) and summary["counts_by_paper"]
assert isinstance(summary["counts_by_class"], dict)
for label in inv.LABELS:
    assert label in summary["counts_by_class"]
assert len(summary["images"]) == summary["total_images"]
for rec in summary["images"]:
    for field in inv.REQUIRED_IMAGE_FIELDS:
        assert field in rec, field
    assert rec["label"] in inv.LABELS
    assert rec["confidence"] == "heuristic"
    # Inventory is metadata only — never stash page text or raster bytes.
    assert "text" not in rec
    assert "pixels" not in rec
    assert "data" not in rec

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "image_inventory.json"
    inv.write_inventory(summary, str(out))
    loaded = __import__("json").loads(out.read_text(encoding="utf-8"))
    assert loaded["total_images"] == summary["total_images"]

assert before == _index_fingerprints(), "inventory must not mutate the search index"
inv.print_summary(summary)
print(
    f"PASS: inventory ran on {summary['pdf_count']} PDF(s); "
    f"{summary['total_images']} images; required fields present; index unchanged"
)
