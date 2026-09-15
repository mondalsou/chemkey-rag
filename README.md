# ChemKeyRAG

Retrieval over chemistry papers keyed on **structure**, not on the name someone
happened to type.

A name search depends on the spelling in the paper. ChemKeyRAG links detected
names to molecular connectivity keys, so a query can retrieve indexed passages
under other names. It reads PDF text; structures shown only in drawings are not
indexed. Name detection and resolution can miss mentions.

No vector database. No embeddings. No trained NER model.

## What the demo measures

Paracetamol coverage in the three-paper index, including reference passages:

```text
Structure                              133
All indexed spellings + current name   133
Only structure finds                     0
Only spellings find                      0
```

A complete synonym list matches structure coverage here. The benefit is searching
without already knowing every spelling. These are overlapping passage sets, not
claims that each name appears in a different paper.

The previous index measured **132 / 133 / 0 / 1**. Recovering the known name in
`Paracetamol-Oxalic` adds the missing structure annotation, giving the counts above.
This extra passage is a reference, not a new experimental result. Rebuild the
index after updating the extraction code. Counts can differ with corpus or cache
contents; the notebook measures the local index rather than assuming these totals.

Caffeine demonstrates a separate benefit: three structure-only passages contain
the PDF-split spelling `ca ffeine`. See the saved notebook for excerpts and the
full set comparison.

## How it works

```
PDF -> page chunks
        |
        +-- candidate names   lexical pass (suffixes, locants, chemical morphemes)
        |        |
        |        +-- resolve  shipped lexicon -> cache -> PubChem PUG-REST
        |                |
        |                +-- normalise   RDKit: largest fragment -> InChIKey
        |                        |
        |                        +-- skeleton block = first 14 chars = join key
        |
        +-- index: {text, page, source_doc, compounds[], blocks[]}

query ---+-- text      -> BM25 over chunk text
         +-- structure -> exact skeleton match, then BM25 rank within the matches
                            |
                            +-- LLM composes a cited answer over retrieved spans only
```

1. **Extract** — `pypdf` reads pages, text is split into ~180-word overlapping windows
2. **Detect** — a lexical pass proposes candidate chemical names. It is deliberately
   dumb: suffix rules (`-amide`, `-ine`, `-ol`, `-oic acid`), locant patterns
   (`1,3,7-`, `N-(`), and a chemical-morpheme rule that catches drug names a suffix
   rule misses (`phenacetin` = *phen* + *acet*)
3. **Resolve** — the resolver is the entity linker. A shipped, RDKit-verified lexicon
   answers first; unknown names fall through to PubChem and get cached
4. **Normalise** — RDKit keeps the largest fragment (strips counter-ions and solvates)
   and hashes to an InChIKey
5. **Connect** — the first block of the InChIKey encodes connectivity only, so
   stereoisomers, salts and hydrate variants of one molecule collapse onto one key
6. **Retrieve** — text route and structure route, side by side
7. **Answer** — every claim cites `[Source N]`; the model is told to refuse rather
   than fill a gap, and never to invent an identifier

## Stack

| Component | Choice | Why |
|-----------|--------|-----|
| Retrieval | BM25, hand-rolled | Interpretable, zero index cost, the honest baseline to beat before paying for vector infrastructure |
| Entity linking | Shipped lexicon + PubChem PUG-REST | Deterministic and offline by default; the network path is a fallback, not a dependency |
| Normalisation | RDKit InChIKey | The only join key that is toolkit-independent |
| LLM | `deepseek/deepseek-v4-flash` via OpenRouter | Cheap; any OpenRouter model is a drop-in |
| UI | Streamlit | One file, no build step |

## Quick start

```bash
git clone https://github.com/mondalsou/chemkey-rag.git
cd chemkey-rag
```

**Reuse an environment you already have.** Anything with RDKit and Streamlit is
close enough — you probably only need `pypdf`:

```bash
conda activate <your-rdkit-env>
python -c "import rdkit, streamlit, pypdf, PIL, requests, dotenv" || pip install -r requirements.txt
```

**Or make a dedicated one:**

```bash
conda env create -f environment.yml
conda activate chemkey-rag
```

Download the three papers listed in [`papers/PAPERS.md`](papers/PAPERS.md) into
`papers/`, then:

```bash
python build_index.py --offline     # lexicon only, no network
streamlit run streamlit_app.py
```

For a written answer, add an OpenRouter key:

```bash
cp .env.example .env                # paste your key
```

Get one at [openrouter.ai/keys](https://openrouter.ai/keys). Retrieval works
without it — only the generated paragraph needs the model.

Or step through the pipeline in the notebook:

```bash
jupyter notebook ChemKey_Research_Demo.ipynb
```

## Project structure

```
chemkey-rag/
├── chemkey.py                   # extraction, resolution, retrieval, answers
├── build_index.py               # papers/*.pdf -> data/index.json
├── make_lexicon.py              # regenerate the verified lexicon
├── streamlit_app.py             # research workspace
├── evidence_plots.py            # verified table parsing and plot rendering
├── ChemKey_Research_Demo.ipynb   # executed demo with saved outputs
├── checks/                     # runnable regression checks
├── scripts/inventory_pdf_images.py  # phase-1 embedded-image inventory
├── scripts/ocr_table_images.py      # phase-2 image-table OCR (optional Tesseract)
├── docs/FIGURE_TABLE_OCR.md     # figure/table OCR (phases 1–2 done; 3 not started)
├── HANDOFF.md                  # measured findings, decisions, known limits
├── papers/PAPERS.md             # source papers and download instructions
├── data/lexicon.json            # shipped name-to-SMILES mapping
├── .streamlit/config.toml       # UI theme
└── requirements.txt
```

For development context and earlier measurements, read [HANDOFF.md](HANDOFF.md).

## Design notes

**Why the skeleton block and not the full InChIKey.** The first 14 characters
encode connectivity only. That is usually what you want — a paper about the
hydrochloride salt is still a paper about the drug. It is also a deliberate loss:
maleic and fumaric acid share a skeleton block. Match on the full key when
stereochemistry is the point, and on the block when the molecule is.

**Why the lexicon is not cheating.** It is the same trade every registry makes:
a curated table answers instantly and is reviewable in a diff; the API is there
for the long tail. `make_lexicon.py` parses, canonicalises and hashes every entry
with RDKit before writing, so a wrong SMILES cannot reach the index silently.

**What a real system would add.** A trained chemical NER model, to catch trade
names, typos and names broken across a line break. OSCAR/OSRA-style structure
extraction from schemes and figures, which is where most of the chemistry in a
paper actually lives. Tautomer canonicalisation policy, versioned — changing it
re-partitions the whole index. And provenance on every resolution, so a bad link
can be traced and retracted rather than silently corrected.

**What it deliberately does not do.** It does not generate structures, and it
does not let the model assert an identifier. A generated CAS Registry Number that
looks plausible is worse than no answer.

## Licence and sources

Code: MIT. The PDFs are not redistributed — `papers/*.pdf` is gitignored, and the
app links to the open-access record rather than serving a copy.

`data/index.public.json` is committed so the app runs from a clone with no build
step. It contains extracted passages from the two CC BY papers only:

- Muzioł, T.M.; Bronikowska, E. *Driving Forces in the Formation of Paracetamol
  Cocrystals and Solvate with Naphthalene, Quinoline and Acridine.* Molecules
  **2024**, 29, 4437. [PMC11434482](https://pmc.ncbi.nlm.nih.gov/articles/PMC11434482/) — CC BY 4.0
- Cysewski, P.; Jeliński, T.; Przybyłek, M.; Nowak, W.; Olczak, M. *Solubility
  Characteristics of Acetaminophen and Phenacetin in Binary Mixtures of Aqueous
  Organic Solvents.* Pharmaceutics **2022**, 14, 2828.
  [PMC9781932](https://pmc.ncbi.nlm.nih.gov/articles/PMC9781932/) — CC BY 4.0

Paluch et al., *J. Chem. Phys.* **2015**, 142, 044508
([PMC4312346](https://pmc.ncbi.nlm.nih.gov/articles/PMC4312346/)) is © AIP
Publishing and is **not** included in the committed index. Download it into
`papers/` and run `python build_index.py` to reproduce the full local corpus —
the app prefers `data/index.json` when it exists.

## Research workspace

The refreshed UI has four views: Evidence explorer (topic and paper filters,
full passages, JSON export), Ask the library (molecule-pinned cited chat),
Retrieval insights (exact-name coverage and BM25 comparisons), and Source
library (original PDF downloads). Chemical-name queries use the resolved
structure; unresolved names stop instead of falling back to an example.

Run checks from the repo root (no network). `check_image_inventory.py` exits
0 with SKIP/OK when `papers/*.pdf` are absent, so a clone without the corpus
still passes:

```bash
python checks/check_candidates.py
python checks/check_workspace.py
python checks/check_chat.py
python checks/check_plots.py
python checks/check_solubility.py
python checks/check_image_inventory.py
python checks/check_table_ocr.py
```

`check_workspace.py` exercises the real local index and mocks only the external
answer service. Coverage metrics include references; ranked retrieval filters
detected reference lists. Topic terms rank matching structures and may fall
back to general structure passages when no topic terms match.
`check_solubility.py` needs a local paper B PDF and `data/index.json`. Image
inventory and image-table OCR write gitignored metadata only and do not rebuild
the index. `check_table_ocr.py` is SKIP/OK without PDFs or Tesseract.
`python build_index.py --image-tables` is opt-in and off by default. See
[docs/FIGURE_TABLE_OCR.md](docs/FIGURE_TABLE_OCR.md).

### Optional plots in chat

Ask “Plot paracetamol solubility in DMSO + water” or open **Plot source data**
below a solubility answer. Switch between composition and temperature, then
download SVG or CSV. Charts parse retrieved Table 1 values directly, including
reported uncertainties and the mole-fraction × 100 scale. Supported systems are
paracetamol in DMSO/DMF/4FM + water and phenacetin in 4FM + water.
Other tables and qualitative coformer findings currently remain text answers.
Rendering runs in an isolated process with a timeout; failed plots leave chat
and sources available. Run `python checks/check_plots.py` to verify.
