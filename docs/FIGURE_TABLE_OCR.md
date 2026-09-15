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
4. **Recovers columns by row vote.** Each row proposes an edge at the midpoint
   of every within-row gap wider than 1.5 line heights; edges within one line
   height of each other are the same column, and an edge is real only once at
   least a third of the rows (minimum two) propose it. A row with a word across
   *every* edge is a caption, not a data row: it is kept whole in its first
   cell and left out of the fill measurement. Projecting the whole raster onto
   x, or single-linkage clustering of word centres, both fail here — one
   full-width caption chains every column into one.
5. **Refuses** rather than guesses when:
   - mean Tesseract **word** confidence &lt; **70** (0–100 scale), or
   - fewer than 2 rows or 2 columns can be clustered from the boxes, or
   - the recovered grid is sparse (fill &lt; 0.40 — typical of plots, not tables), or
   - no tokens, or the raster would not decode.
   Refused records have `cells: null`. Empty cells in an accepted grid stay
   empty strings. Cell values are never invented.
6. Writes gitignored `data/table_ocr.json`, retains every attempted raster under
   gitignored `data/extracted_tables/`, and — under `--image-tables` — adds each
   accepted grid to `data/index.json` as one passage
   (`extraction_method: "table_ocr"`) whose chemical names are resolved like any
   other passage. Refused rasters stay reviewable but are never indexed.

Accepted records carry `column_boundaries` (the voted x edges) and
`spanning_rows` alongside `cells`, so a reviewer can see why a cell landed
where it did.

Measured on a rasterised scan of Table 1 from the LayoutParser paper
(2160x510 px, one page, image-only): 74 words at mean confidence 90.09,
recovered as a **7x4** grid with edges at x = 386.5, 650.5, 884.0 and row 0
marked spanning. All five data rows and the header align to the correct
columns. Before the row vote the same input recovered one column and was
refused with `grid_not_recovered`. This is a single fixture, not a benchmark:
tables with no consistent gutter (fully ruled cells, wrapped multi-line cells,
or rotated scans) are still expected to refuse.

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
python build_index.py --image-tables    # + accepted grids as searchable passages
```

### In the app

The **Image tables** tab mirrors Image structures: counts of rasters attempted,
grids accepted, and passages added; then one card per attempted raster with its
retained crop, the recovered grid (spanning rows shown italic and merged), the
voted column edges, and the raw OCR text before layout recovery. Refused rasters
show the crop and the refusal reason with no cells, which is the point — the
gate is visible, not hidden.

The corpus has no image-only table of its own, so the tab needs the scan-like
fixture described in `papers/PAPERS.md`; without it the tab shows nine attempted
rasters, eight of them plots that correctly refuse on confidence.

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

## Phase 3 (implemented, opt-in) — structure drawings

`scripts/recognize_structure_images.py` provides an end-to-end local OCSR path:

1. Render complete PDF pages at 300 DPI, so vector drawings are visible.
2. Use DECIMER-Segmentation to crop molecular depictions from pages, schemes and
   tables. Embedded rasters are also inspected as a second route.
3. Use DECIMER to translate each crop into SMILES.
4. Refuse empty, unparsable or very small predictions. RDKit canonicalises every
   accepted prediction and generates the full InChIKey plus connectivity block.
5. Retain the original crop under `data/extracted_structures/` and provenance in
   `data/structure_ocr.json`.
6. Add one structure-searchable anchor per accepted crop to `data/index.json`.
   The anchor proves only that a depiction was recognized on that page; it does
   not turn nearby prose into a verified property claim.

The optional model stack is deliberately separate from the lightweight app:

```bash
python -m venv .venv-ocsr
source .venv-ocsr/bin/activate
pip install -r requirements.txt -r requirements-structure-ocr.txt
python build_index.py --offline --structure-images --structure-source both
python checks/check_structure_ocr.py
```

The Streamlit **Image structures** tab displays each matching source crop beside
the RDKit rendering reconstructed from the predicted SMILES. A chemically valid
SMILES is necessary but not proof that the drawing was read correctly, so the
visual comparison remains part of the interface.

CAS does not publish an installable image-recognition model. This implementation
reproduces the relevant workflow pattern—depiction detection, graph recognition,
chemical validation, connectivity search and human-visible provenance—using the
open DECIMER stack. It must not be described as CAS technology.

## Licensing

Sidecar JSON can contain OCR of local PDFs. It is gitignored. Do not copy paper C
(AIP) text into a committed public file.
