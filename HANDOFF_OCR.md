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

Measured result: 74 OCR words, mean confidence 90.09%, readable raw table text, but `status: refused`, `reason: grid_not_recovered`, with one detected column. This is the current known limitation: text OCR works, while generic column/grid reconstruction still needs refinement. Do not describe this fixture as a successful structured-table extraction yet.

The table OCR implementation is in `scripts/ocr_table_images.py`. It uses Tesseract word bounding boxes, confidence threshold 70, minimum 2x2 grid, and refuses rather than inventing cells when layout recovery is unreliable.

## Next recommended work

1. Improve `recover_grid` using image projection/vertical-rule detection or an adaptive multi-column model, then rerun `run_table_demo.py`.
2. Add a Table OCR review tab to Streamlit only after a real accepted grid exists; show source crop, recovered cells, confidence, and refusal reasons.
3. Keep all candidate crops and raw OCR output for auditability.
4. Commit and push only to `feature/figure-table-ocr`; keep `main` protected and unmerged.

## Important claims and limits

- This is not CAS's proprietary OCR model. CAS does not publish an installable equivalent; DECIMER is the local chemical-structure OCR choice.
- Structure OCR is probabilistic and is intended as a useful capability, not perfection.
- Largest-fragment cleanup is a heuristic and can be wrong for mixtures, co-crystals, or similarly sized components.
- Page-level chunking does not repair words split across PDF pages.
- Tesseract can recognise scanned text, but table structure recovery is a separate, harder step.
