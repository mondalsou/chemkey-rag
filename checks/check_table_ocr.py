"""Run: python checks/check_table_ocr.py

No network. Does not mutate data/index.json. Clone-friendly: SKIP/OK when
papers/*.pdf or Tesseract are absent. Fixtures/mocks cover skip-thin-strip,
refuse-on-low-confidence, and that native pypdf table text is untouched.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inventory_pdf_images as inv
import ocr_table_images as ocr

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


def _word(text, conf, left, top, width=40, height=16):
    return {
        "text": text,
        "conf": conf,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


before = _index_fingerprints()

# --- thin MDPI v2 table RULES are skipped, never OCR'd ---
assert inv.is_thin_table_rule(1601, 5)
assert inv.is_thin_table_rule(1601, 3)
assert not inv.is_thin_table_rule(1588, 677)
assert ocr.skip_reason_for_record(
    {"label": "table_image", "width": 1601, "height": 5}
) == "thin_table_rule"
assert ocr.skip_reason_for_record(
    {"label": "structure_drawing", "width": 800, "height": 800}
) == "not_table_image"
assert ocr.skip_reason_for_record(
    {"label": "table_image", "width": 1588, "height": 677}
) is None
assert ocr.skip_reason_for_record(
    {"label": "table_image", "width": 80, "height": 20}
) == "not_table_like"

# --- refuse rather than guess: low Tesseract confidence yields no cells ---
low = [
    _word("1.23", 20, 10, 10),
    _word("4.56", 25, 80, 10),
    _word("7.89", 18, 10, 40),
    _word("0.12", 22, 80, 40),
]
low_result = ocr.decide_ocr_result(low, confidence_threshold=ocr.CONFIDENCE_THRESHOLD)
assert low_result["status"] == "refused", low_result
assert low_result["reason"] == "low_confidence"
assert low_result["cells"] is None
assert low_result["mean_confidence"] < ocr.CONFIDENCE_THRESHOLD

# --- grid cannot be recovered (one column) → refuse, do not invent columns ---
one_col = [
    _word("alpha", 90, 10, 10),
    _word("beta", 91, 12, 40),
    _word("gamma", 88, 11, 70),
]
gridless = ocr.decide_ocr_result(one_col)
assert gridless["status"] == "refused", gridless
assert gridless["reason"] == "grid_not_recovered"
assert gridless["cells"] is None

empty = ocr.decide_ocr_result([])
assert empty["status"] == "refused" and empty["reason"] == "no_text"
assert empty["cells"] is None

# --- recoverable grid is accepted; empty cells stay empty, never filled ---
good = [
    _word("A", 90, 10, 10),
    _word("B", 92, 120, 10),
    _word("1.0", 88, 10, 50),
    _word("2.0", 91, 120, 50),
]
accepted = ocr.decide_ocr_result(good)
assert accepted["status"] == "accepted", accepted
assert accepted["cells"] == [["A", "B"], ["1.0", "2.0"]], accepted["cells"]
assert accepted["n_rows"] == 2 and accepted["n_cols"] == 2

# High confidence but a sparse plot-like grid → refuse, do not publish cells
sparse = [_word(f"C{i}", 92, 10 + i * 90, 10) for i in range(8)]
for y in (50, 90, 130):
    sparse.append(_word("x", 91, 10, y))
sparse_result = ocr.decide_ocr_result(sparse)
assert sparse_result["status"] == "refused", sparse_result
assert sparse_result["reason"] == "sparse_grid"
assert sparse_result["cells"] is None

# --- mocked OCR path: thin strip still skipped even if an OCR fn is supplied ---
def fake_high(_img):
    return {
        "level": [5, 5, 5, 5],
        "conf": [90, 90, 90, 90],
        "text": ["A", "B", "1", "2"],
        "left": [10, 120, 10, 120],
        "top": [10, 10, 50, 50],
        "width": [20, 20, 20, 20],
        "height": [16, 16, 16, 16],
    }


strip = {
    "paper_id": "A",
    "filename": "A_paracetamol_cocrystals.pdf",
    "page": 8,
    "image_index": 0,
    "width": 1601,
    "height": 5,
    "label": "table_image",
    "xobject_name": "/Im100",
}
skipped = ocr.ocr_table_record(strip, str(PAPERS), ocr_fn=fake_high)
assert skipped["status"] == "skipped" and skipped["reason"] == "thin_table_rule"
assert skipped["cells"] is None

# --- native-text table path is untouched (pypdf extract_text, no Tesseract) ---
chemkey_src = (ROOT / "chemkey.py").read_text(encoding="utf-8")
assert "page.extract_text()" in chemkey_src
assert "pytesseract" not in chemkey_src
assert "image_to_data" not in chemkey_src
assert "ocr_table_images" not in chemkey_src
plots_src = (ROOT / "evidence_plots.py").read_text(encoding="utf-8")
assert "source['text']" in plots_src
assert "pytesseract" not in plots_src
assert "table_ocr.json" not in plots_src

# --- ingest flag exists, defaults OFF (store_true), sidecar only ---
build_src = (ROOT / "build_index.py").read_text(encoding="utf-8")
assert "--image-tables" in build_src
assert "store_true" in build_src
assert "ck.build_index" in build_src
assert "ck.save_index" in build_src
# Do not collide with a page-level --ocr auto fallback (separate feature).
assert "--ocr" not in build_src

assert ocr.CONFIDENCE_THRESHOLD == 70.0

assert before == _index_fingerprints(), "table OCR check must not mutate the search index"

pdfs = inv.find_pdfs(str(PAPERS))
tess = ocr.tesseract_available()
if not pdfs:
    print(
        "SKIP/OK: no papers/*.pdf; clone-friendly. "
        "Thin-strip skip, refuse-on-low-confidence, native-text path assertions passed."
    )
    sys.exit(0)
if not tess:
    print(
        "SKIP/OK: Tesseract/pytesseract not installed. "
        "Fixture assertions passed; live image-table OCR skipped. "
        "See requirements-table-ocr.txt."
    )
    sys.exit(0)

# Live run writes only a temp sidecar; index fingerprints must hold.
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "table_ocr.json"
    inventory_path = ROOT / "data" / "image_inventory.json"
    summary = ocr.run_table_ocr(
        papers_dir=str(PAPERS),
        inventory_path=str(inventory_path) if inventory_path.exists() else "",
        out_path=str(out),
        dry_run=False,
    )
    assert summary is not None
    counts = summary["counts"]
    assert counts["skipped_thin_table_rule"] > 0, counts
    assert counts["table_image_candidates"] == counts["skipped"] + counts["ocr_attempted"]
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["counts"]["skipped_thin_table_rule"] == counts["skipped_thin_table_rule"]
    for row in loaded["tables"]:
        if row["reason"] == "thin_table_rule":
            assert row["cells"] is None
            assert row["status"] == "skipped"
        if row["status"] == "accepted":
            assert row["cells"], row
            assert row["mean_confidence"] >= ocr.CONFIDENCE_THRESHOLD
        if row["status"] == "refused":
            assert row["cells"] is None
            assert row["reason"]

assert before == _index_fingerprints(), "live table OCR must not mutate the search index"
ocr.print_summary(summary)
print(
    f"PASS: skip/refuse fixtures; native pypdf tables untouched; "
    f"live OCR skipped={counts['skipped_thin_table_rule']} thin rules, "
    f"accepted={counts['accepted']}, refused={counts['refused']}; index unchanged"
)
