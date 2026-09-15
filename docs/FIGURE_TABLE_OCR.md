# Figure / table OCR — status

Phase 1 (this branch) is **inventory + tooling only**. No DECIMER, MolScribe,
Tesseract, or other heavy OCR models are downloaded or called. Image bytes are
not written to disk. The search index is not updated.

## Phase 1 (done)

`scripts/inventory_pdf_images.py` scans `papers/*.pdf` with the same `pypdf`
stack used for text ingest, lists every embedded image, and applies a crude
heuristic label:

| Label | Heuristic |
|---|---|
| `table_image` | Very wide aspect plus many horizontal / grid-like rows, **or** a thin strip (height ≤ 16 px and aspect ≥ 8). MDPI v2 of paper A stores hundreds of table rules as ~1601×5 rasters. |
| `structure_drawing` | Near-square, high white fraction, sparse dark ink, low chroma (line art). |
| `scheme_or_reaction` | Other line-art composites, often wide. |
| `other` | Logos, photos, dense colour (including the MDPI header banner). |
| `unknown` | Missing dimensions and pixels that would not decode. |

Every record is tagged `confidence: "heuristic"`. The JSON summary is written to
`data/image_inventory.json` (gitignored). Stdout prints counts by paper and by
class. No page text is stored, so a local paper C (AIP) scan cannot leak into a
committed public index.

Pillow is used only to measure those raster stats. It is already a Streamlit
dependency; it is listed explicitly because the inventory imports it.

### Run

```bash
python scripts/inventory_pdf_images.py          # skip if papers/*.pdf missing
python checks/check_image_inventory.py          # SKIP/OK on a clone without PDFs
```

With the MDPI **v2** PDF for paper A (`molecules-29-04437-v2.pdf` saved as
`A_paracetamol_cocrystals.pdf`), the inventory reports **692** embedded images,
matching the HANDOFF figure. The earlier/smaller publisher PDF of the same
article has only ~25 XObjects. A different export can therefore disagree; the
script reports what is actually embedded in the files on disk.

## Phase 2 (planned) — table OCR

Run OCR on `table_image` candidates (and stitch the thin-strip tables in paper A)
so numeric grids become text the existing table parser can read. Still optional,
still local. Not Tesseract-in-ingest until measured.

## Phase 3 (planned) — structure drawings

DECIMER / MolScribe (or equivalent) on `structure_drawing` candidates, then the
same InChIKey skeleton join used for names. This is the path that would give a
molecule that is **only drawn** a retrieval key.

## Ingest flag

Phases 2–3 stay **off** the default `build_index.py` path. A future optional
flag (for example `--extract-figures`) will gate them so a clone and a routine
rebuild do not download models or change chunk text. Phase 1 does not add that
flag; it only produces the inventory needed to size the work.
