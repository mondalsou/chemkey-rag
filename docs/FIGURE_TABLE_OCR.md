# Figure / table OCR — status

## Phase 1 (done) — inventory

`scripts/inventory_pdf_images.py` scans `papers/*.pdf` with the same `pypdf`
stack used for text ingest, lists every embedded image, and applies a crude
heuristic label. JSON: gitignored `data/image_inventory.json`.

| Label | Heuristic |
|---|---|
| `table_image` | Very wide aspect plus many horizontal / grid-like rows, **or** a thin strip (height ≤ 16 px and aspect ≥ 8). MDPI v2 of paper A stores hundreds of table **rules** as ~1601×5 rasters. |
| `structure_drawing` | Near-square, high white fraction, sparse dark ink, low chroma (line art). |
| `scheme_or_reaction` | Other line-art composites, often wide. |
| `other` | Logos, photos, dense colour (including the MDPI header banner). |
| `unknown` | Missing dimensions and pixels that would not decode. |

```bash
python scripts/inventory_pdf_images.py
python checks/check_image_inventory.py          # SKIP/OK without PDFs
```

The MDPI **v2** PDF for paper A reports **692** embedded images. A different
export of the same article can show ~25 XObjects.

## Phase 2 (done) — image-table OCR, refuse-on-low-confidence

`scripts/ocr_table_images.py` reads `table_image` rows from the inventory (or
rebuilds it) and:

1. **Skips thin table rules.** `is_thin_table_rule(width, height)` is true when
   height ≤ 16 px and aspect ≥ 8 (or the tall/thin analogue). Those rasters are
   ruled lines in MDPI v2 packaging, not tables. They are never sent to Tesseract.
2. **Skips** leftovers that are too small to be a table (`MIN_OCR_WIDTH` 120,
   `MIN_OCR_HEIGHT` 40) and anything not labeled `table_image`. Structure
   drawings are not OCR'd (phase 3).
3. **Extracts** the remaining rasters in memory via pypdf (no image files
   written). Tesseract `--psm 6` plus word bounding boxes recover a grid.
4. **Refuses** rather than guesses when:
   - mean Tesseract **word** confidence &lt; **70** (0–100 scale), or
   - fewer than 2 rows or 2 columns can be clustered from the boxes, or
   - the recovered grid is sparse (fill &lt; 0.40 — typical of plots, not tables), or
   - no tokens, or the raster would not decode.
   Refused records have `cells: null`. Empty cells in an accepted grid stay
   empty strings. Cell values are never invented.
5. Writes gitignored `data/table_ocr.json`. **Does not mutate** `data/index.json`.

Native/digital tables already come from `page.extract_text()` in
`chemkey.extract_pdf_chunks`. Phase 2 does not replace that path.
`evidence_plots.py` still parses retrieved Table 1 **text**.

### Run

```bash
# optional extra (not in the default Streamlit env)
# sudo apt-get install tesseract-ocr tesseract-ocr-eng
pip install -r requirements-table-ocr.txt

python scripts/ocr_table_images.py
python checks/check_table_ocr.py        # SKIP/OK without PDFs or Tesseract
```

Ingest flag, **off by default**:

```bash
python build_index.py                   # native text only (demo path)
python build_index.py --image-tables    # same index, plus table_ocr.json sidecar
```

`--image-tables` never substitutes Tesseract for `extract_text()`. A page-level
sparse OCR auto-fallback, if present on another branch, is a different feature;
this sidecar is not named `--ocr`.

### Local measurement (paper A v2 + paper B)

Tesseract 5.3.4 was available on the measurement VM.

| | Count |
|---|---|
| `table_image` candidates | 667 |
| skipped thin table rules | **660** |
| OCR attempted | 7 |
| accepted grids | **0** |
| refused (mean confidence &lt; 70) | 7 |

Paper B's remaining candidates are figure/plot rasters (COSMO-RS, axis labels),
not numeric tables. Refusing them is correct. Paper B Table 1 is already digital
text via pypdf on page 10. Paper C was not present.

## Phase 3 (not started) — structure drawings

DECIMER / MolScribe on `structure_drawing` candidates, then the InChIKey skeleton
join used for names. Not imported in phase 2. Stays behind a future optional
ingest flag. Demo copy must not claim figure OCR is production.

## Licensing

Sidecar JSON can contain OCR of local PDFs. It is gitignored. Do not copy paper C
(AIP) text into a committed public file.
