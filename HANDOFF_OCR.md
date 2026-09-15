# ChemKeyRAG OCR handoff

Updated: 2026-09-15

This note is intended to let another harness continue the work without replaying the conversation.

## Repository and Git state

- Repository: `/Users/sourav/personal-github/chemkey-rag`
- OCR worktree: `/Users/sourav/personal-github/chemkey-rag-ocr`
- Branch: `feature/figure-table-ocr`
- Latest pushed commit: `6e74118` (`Compare accepted OCR crops with recognised structures`)
- The branch tracks `origin/feature/figure-table-ocr`.
- `main` is protected on GitHub. Do not merge this branch into `main` unless explicitly requested.
- The original main worktree is dirty and must not be reset or overwritten.

## What is implemented

Structure-image OCR is implemented in `scripts/recognize_structure_images.py` using DECIMER-Segmentation and DECIMER, with RDKit validation and InChIKey normalisation. Each candidate retains source-page/crop provenance, raw prediction, accepted/refused status, and refusal reason.

`chemkey.py` stores all candidates for audit, but only accepted OCSR records become searchable structure anchors. `build_index.py` supports `--structure-images`, `--structure-source`, and `--structure-ocr-out`.

`streamlit_app.py` has an Image structures tab. For accepted candidates it shows the source crop beside the reconstructed molecule; refused candidates show the crop, reason, and raw prediction.

Supporting files include `requirements-structure-ocr.txt`, README documentation, checks, and `.gitignore` entries for local demo/cache data.

## Verified molecule demo

The clean public-paper crop is Figure 1(a) from Shende et al., *Drug Stability Analysis by Raman Spectroscopy* (Pharmaceutics, 2014), an acetaminophen/paracetamol structure.

DECIMER raw output included a spurious `.CC` fragment; the pipeline's largest-fragment/RDKit normalisation produced:

```text
CC(=O)Nc1ccc(O)cc1
InChIKey block: RZVAJINKPMORJF
```

The local dry-run index contains 4 papers, 247 chunks, 134 structure passages, and one accepted image-structure match for `raman_acetaminophen.pdf`, page 4.

## UI

The Streamlit app is running at [http://127.0.0.1:8504](http://127.0.0.1:8504). Existing PTY session: `79104`.

Start it again from the OCR worktree if needed:

```bash
.venv-ocsr/bin/python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8504 --server.headless true
```

## Scanned-table experiment

Tesseract 5.5.3 was installed into the existing Conda environment `chemkey-rag` (Homebrew installation failed because this Mac lacked Xcode C++ headers). Verify with:

```bash
conda run -n chemkey-rag tesseract --version
```

Local ignored fixture and runner are under `data/ocr_demo/`:

- `scanned_table_demo.pdf`: one-page image-only PDF made by rasterising Table 1 from the public LayoutParser paper. It is a scan-like fixture, not an original camera/scanner capture.
- `run_table_demo.py`: runs Tesseract and the current table decision gate.
- `table_ocr_result.json`: latest result.

Run:

```bash
PYTHONPATH=. conda run -n chemkey-rag python data/ocr_demo/run_table_demo.py
```

Measured result (2026-09-15, after the column-recovery fix): 74 OCR words, mean confidence 90.09%, `status: accepted`, grid **7x4**, column edges at x = 386.5, 650.5, 884.0, `spanning_rows: [0]`. The caption is kept whole in row 0; the header and all five data rows align to the correct columns:

```text
Table 1: Current layout detection models in the LayoutParser model zoo | | |
Dataset | | Base Model'| | Large Model | | Notes
PubLayNet [38] | F/M | M | Layouts of modern scientific documents
PRImA [3] | M | - | Layouts of scanned modern magazines and scientific reports
Newspaper [17] | F | - | Layouts of scanned US newspapers from the 20th century
TableBank [18] | F | F | Table region on modern scientific and business document
HJDataset [31] | F/M | . | Layouts of history Japanese documents
```

The stray `|` tokens and the `.` for `-` are raw Tesseract output on the ruled lines; they are not cleaned up, because guessing at them would mean inventing cell content. This is one fixture, not a benchmark.

The table OCR implementation is in `scripts/ocr_table_images.py`. It uses Tesseract word bounding boxes, confidence threshold 70, minimum 2x2 grid, and refuses rather than inventing cells when layout recovery is unreliable.

Column recovery votes per row: each row proposes an edge at the midpoint of every within-row gap wider than `COLUMN_GAP_SCALE` (1.5) line heights; proposals within one line height merge; an edge is accepted only if at least `COLUMN_SUPPORT_FRACTION` (1/3, minimum 2) of the rows propose it. A row carrying a word across *every* edge is treated as a caption, kept whole in its first cell, and excluded from the fill-fraction measurement. The previous single-linkage clustering of word centres was what collapsed the table to one column: the caption row chained every column together.

## Image tables in the app

`--image-tables` now attaches accepted grids to the index and the Streamlit app
has an **Image tables** tab beside Image structures: rasters attempted, grids
accepted, passages added, then one card per attempted raster with its retained
crop (`data/extracted_tables/`, gitignored), the recovered grid, the voted
column edges, and the raw OCR text.

The corpus has no image-only table, so the demo needs a fixture. Rebuild it with:

```bash
python scripts/make_scanned_table_fixture.py   # -> papers/D_raman_table1_scan.pdf
python build_index.py --image-tables
```

That rasterises Table 1 of the Raman paper (p-aminophenol mixed with
acetaminophen) into a one-page image-only PDF. Measured on it: 84 words at 92.98
mean confidence, accepted as a **15x4** grid, the three caption lines and the
`% p-Aminophenol` group header marked spanning, all data rows correct. The
passage resolves `acetaminophen` to `RZVAJINKPMORJF`, so a paracetamol structure
query reaches a table that exists only as pixels. It is scan-like, not an
original scanner capture.

Local index after the rebuild: 5 papers, 281 chunks, 1 accepted image table, 1
accepted image structure. `--structure-images` was not re-run; the cached
`data/structure_ocr.json` was reattached with `ck.attach_image_structures`.

## Review fixes applied (commit 3 on this branch)

A high-effort review of the first two commits found seven issues; all are fixed:

- `table_rows_to_text` dropped empty cells, shifting every later value one
  column left in the indexed passage. Positions are preserved now. This was the
  one place the "never invent a cell value" contract leaked.
- `--dry-run` still wrote PNG crops; it now writes nothing.
- `print_summary` and the sidecar `notes` still said "search index unchanged"
  one line after changing it.
- Table name resolution was uncapped against PubChem on OCR text; capped at
  `MAX_TABLE_NAMES` (25) per table.
- `_save_crop` sat outside the try/finally, so an unwritable crops dir leaked
  the PIL handle and killed the whole build before `save_index`.
- The crop assertion fired on a legitimate `decode_failed` refusal.
- `fill_fraction` excludes spanning rows, so it can read 1.0 on a mostly
  collapsed table. `spanning_fraction` is recorded beside it and shown in the
  tab (the fixture reports 26.7%).

The cap fix introduced a `TypeError` of its own — `candidate_names` returns a
set, not a list — which killed `--image-tables`. Fixed, and the fixture check
now passes an offline resolver so that path is actually exercised.

## Next recommended work

1. Handle cells that wrap onto a second line. Row clustering is still one line per row, so a wrapped Notes cell becomes an extra row with only one column filled. A second fixture with wrapped cells would measure how far off this is.
2. `checks/check_workspace.py` fails on `assert at.metric[0].value == '133'`. It was already failing before this work: that count is pinned to the original three-paper corpus, and the local index has had a fourth paper since the structure-OCR session. Re-pin it or scope it to the three committed papers.
3. Keep all candidate crops and raw OCR output for auditability.
4. Commit and push only to `feature/figure-table-ocr`; keep `main` protected and unmerged.

## Important claims and limits

- This is not CAS's proprietary OCR model. CAS does not publish an installable equivalent; DECIMER is the local chemical-structure OCR choice.
- Structure OCR is probabilistic and is intended as a useful capability, not perfection.
- Largest-fragment cleanup is a heuristic and can be wrong for mixtures, co-crystals, or similarly sized components.
- Page-level chunking does not repair words split across PDF pages.
- Tesseract can recognise scanned text; table structure recovery is a separate step that now works on one clean fixture. It assumes a consistent vertical gutter between columns, one line per row, and an upright scan. Fully ruled tables, wrapped multi-line cells, and rotated scans are expected to refuse or mis-row, and have not been tested.
- The corpus rasters in `papers/` still all refuse with `low_confidence` (7 of 7 attempted); the column fix changes nothing there, because they never reach grid recovery.
