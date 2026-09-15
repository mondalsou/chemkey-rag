# ChemKeyRAG — handoff

State as of 2026-09-07. Written so the project can be picked up cold, from any
harness, without replaying the conversations that produced it.

## What it is

Retrieval over chemistry papers keyed on **structure**, not on the name an author
happened to type. Every chemical name in every chunk is resolved to an InChIKey
skeleton at ingest and stored beside the text, so one molecule retrieves every
passage about it. No vector database, no embeddings, no trained NER.

Public repo: <https://github.com/mondalsou/chemkey-rag> (MIT).
Featured on the portfolio site at `mondalsou/mondalsou`, project card 6.

## Run it

```bash
conda env create -f environment.yml     # or: conda activate chemkey-rag
streamlit run streamlit_app.py          # http://localhost:8501
python checks/check_workspace.py        # and the other checks under checks/
python checks/check_image_inventory.py  # SKIP/OK if papers/*.pdf are absent
python checks/check_table_ocr.py        # SKIP/OK without PDFs or Tesseract
```

A clone runs immediately — `data/index.public.json` is committed. Only rebuild
if you want the full three-paper corpus.

### The two indexes, and why

| File | Papers | Committed | Used when |
|---|---|---|---|
| `data/index.json` | A, B, C | no — gitignored | present locally; preferred |
| `data/index.public.json` | A, B | **yes** | fallback, so a clone/deploy works |

`streamlit_app.py` picks `PRIVATE_INDEX if os.path.exists(...) else PUBLIC_INDEX`.

This split exists for licensing. The index stores **verbatim extracted text**, so
committing it republishes the papers. A and B are MDPI, CC BY 4.0 — fine with
attribution (given in the README). **Paper C is © AIP Publishing**, in PMC under
NIH public access, which permits reading but not redistribution, so its text is
excluded from the committed index. Keep it that way: the build command writes
`data/index.json` (ignored), never the public file, so a routine rebuild cannot
leak it.

Rebuild the public index deliberately:

```bash
mkdir -p /tmp/pub && ln -s "$PWD"/papers/{A_*,B_*}.pdf /tmp/pub/
python build_index.py --papers /tmp/pub --out data/index.public.json
```

### Corpus

PDFs are not redistributed (`papers/*.pdf` gitignored); the app links to the PMC
record rather than serving a copy. PMC blocks scripted download with a
proof-of-work challenge — **Europe PMC works**:

```bash
curl -sL -A "Mozilla/5.0" --retry 6 --retry-delay 25 --retry-all-errors \
  -o papers/A_paracetamol_cocrystals.pdf \
  "https://europepmc.org/articles/PMC11434482?pdf=render"
# B = PMC9781932, C = PMC4312346   (see papers/PAPERS.md)
```

A rate-limited request returns a 20-byte text file, not a PDF. Check page 1 text
with `pypdf` before indexing.

## Layout

```
chemkey.py          ingest, resolve, normalise, retrieve, answer
streamlit_app.py    four-tab workspace
evidence_plots.py   charts parsed from retrieved Table 1 text only, never LLM prose
build_index.py      papers/*.pdf -> index json
make_lexicon.py     regenerates + RDKit-verifies data/lexicon.json
scripts/inventory_pdf_images.py   phase-1 figure/table image inventory (no index writes)
scripts/ocr_table_images.py       phase-2 image-table OCR sidecar (Tesseract optional)
checks/             runnable checks, no API calls, no index mutation
docs/FIGURE_TABLE_OCR.md          phases 1–2 done; phase 3 (DECIMER/MolScribe) not started
```

App tabs: **Evidence explorer** (filters, passages, JSON export) · **Ask the
library** (molecule-pinned cited chat) · **Retrieval insights** (name coverage vs
BM25) · **Source library** (PMC links).

### Two retrieval paths — do not confuse them

- `conversation_search()` — **what chat uses.** Restricts candidates to the
  connectivity block *before* ranking, so an answer can never drift to another
  molecule. Interleaves the current-question ranking with a history-aware one,
  but only when the question looks like a follow-up (pronoun, or ≤4 content
  terms). No whole-corpus text fallback.
- `hybrid_search()` — text route + structure route interleaved, kept for the
  side-by-side comparison views. **Not** used for chat.

`expand_source_pages()` returns the whole page around a matched anchor, so a
numeric table continuation that never repeats the compound name still arrives.
`retrieval_query()` carries up to 3 prior user turns for follow-ups.

## What the demo proves

Measured, not asserted. `only structure` = chunks the structure route finds that
**no** name string can.

Full index (A+B+C):

| compound | structure | best single name | all spellings OR'd | only structure |
|---|---|---|---|---|
| Paracetamol | 133 | 83 | 133 | 0 |
| Phenacetin | 48 | 47 | 48 | 0 |
| Acetanilide | 19 | 19 | 19 | 0 |
| Caffeine | 10 | 7 | 7 | **3** |
| Benzocaine | 8 | 8 | 8 | 0 |

Two claims, unequal strength:

1. **Query-side invariance — strong, everywhere.** `4-hydroxyacetanilide` returns
   **0** chunks by text and **133** by structure (115 on the public index). Same
   for `tylenol`, `4-acetamidophenol`. You need not know the synonyms.
2. **Beyond any name — narrow but real.** Caffeine only, +3 chunks, because the
   PDF wrote `ca ffeine` and no string search can reach it. This is the *only*
   place structure beats a perfect synonym list — **and it lives entirely in
   paper C, so it is absent from the public index.**

No chunk in this corpus uses an IUPAC name alone; each appears as a gloss beside
the trivial name. Demonstrating the corpus-side claim needs a fourth paper.

## Bugs fixed (each left a check behind)

Ingest: line-broken names (`parace-\ntamol`) rejoined by `repair_page_text()`;
stray intra-word spaces (`ca ffeine`) recovered by lexicon-gated token rejoin;
`resolve()` made case-insensitive at its single choke point; `--max-candidates`
default raised 300 → 500 (47 names were being dropped silently).

Retrieval: **BM25 ranked question boilerplate over content** (`compound` idf 3.97
vs `solubility` 0.95) — fixed by `query_terms()`; `hybrid_search` was
structure-only because structure filled `top_k` first; **bibliographies dominated
results** — they repeat every compound and topic word while carrying no findings,
so `looks_like_bibliography()` excludes 32/246 chunks; **retrieval had no
conversation memory** while the prompt did, so `"Only DMSO+water?"` lost the topic.

Answers: **drifted to other molecules** — paper B studies acetaminophen and
phenacetin side by side, so `subject=` now pins label/skeleton/names and each
source is tagged `SUBJECT: yes/NO`.

App: `load_dotenv()` was never called, so `.env` was inert; the sidebar key widget
kept its first empty value (`typed_key or env_key`); RDKit's SVG XML prolog
rendered as stray text.

The recurring shape: **the chat had memory and scoping that the machinery beneath
it did not.** Check that first.

## Ground truth worth keeping (verified against the PDFs)

Solvent results come in three kinds, and collapsing them is the main way answers
mislead — the prompt now forces the distinction:

- **Measured in paper B:** DMF, DMSO, 4FM neat; DMSO+water, DMF+water, 4FM+water
- **Literature collection (paper B):** ~20 neat solvents and ~10 binaries,
  including ethanol+water
- **Predicted only (paper B):** ethyltriglycol, methyltriglycol, 2-pyridin-2-ylethanol
- **Simulated only (paper C):** water/ethanol — that paper's entire scope

Table 1 (paper B, p10) holds four systems — `A+DMSO+water`, `A+DMF+water`,
`A+4FM+water`, `P+4FM+water` — each at 25/30/35/40 °C. Values verified against
raw PDF text. There is **no** numeric ethanol/water value for paracetamol
anywhere in the corpus; a refusal there is correct, not a failure.

## Known limits (deliberate)

- **Embedded images are not in the default index.** Phase 1 inventories them
  (`scripts/inventory_pdf_images.py`). Phase 2 OCRs image-only **tables** with
  Tesseract, skipping MDPI v2 ~1601×5 rule strips and refusing below mean
  confidence 70, when a grid cannot be recovered, or when the grid is sparse
  (`scripts/ocr_table_images.py`, gitignored `data/table_ocr.json`). Native
  `page.extract_text()` tables are unchanged. `--image-tables` on
  `build_index.py` is **off by default**. Phase 3 (DECIMER/MolScribe on
  drawings) is not started. See `docs/FIGURE_TABLE_OCR.md`.
- One reference chunk on B p19 escapes the bibliography filter — it starts
  mid-citation. Tightening began eating body text.
- The corpus favours common names, so text search is a genuinely strong baseline.
- Answer latency 4–45 s, dominated by the model, not by retrieval.

## Next steps

1. A fourth paper that names paracetamol only systematically — turns claim 2 from
   a single caffeine artifact into a corpus-side result.
2. Phase 3: DECIMER / MolScribe on `structure_drawing` candidates, still behind
   an optional ingest flag. Do not add those models yet. Image-table OCR
   (phase 2) is a sidecar only.
3. Trained chemical NER to replace the lexical candidate pass.
4. Deploy: **Streamlit Community Cloud** (set `OPENROUTER_API_KEY` in secrets).
   Not Vercel — serverless has no long-lived WebSocket for Streamlit, and the
   dependencies alone are ~106 MB against a 250 MB cap.

## Conventions

- Commits: author **mondalsou** only, no co-author trailers.
- Every non-trivial change leaves one runnable check; retrieval claims are
  verified by measurement, not assertion.
- Root-cause fixes at the single choke point, not per-caller patches.
- Refusal is a correct answer; the prompt forbids inventing identifiers or values.
