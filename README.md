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
python -c "import rdkit, streamlit, pypdf, requests, dotenv" || pip install -r requirements.txt
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

## Licence

MIT. The papers are not redistributed — `papers/*.pdf` is gitignored.

## Research workspace

The refreshed UI has four views: Evidence explorer (topic and paper filters,
full passages, JSON export), Ask the library (molecule-pinned cited chat),
Retrieval insights (exact-name coverage and BM25 comparisons), and Source
library (original PDF downloads). Chemical-name queries use the resolved
structure; unresolved names stop instead of falling back to an example.

Run the UI regression checks with `python checks/check_workspace.py`. These
exercise the real local index and mock only the external answer service.
Coverage metrics include references; ranked retrieval filters detected reference
lists. Topic terms rank matching structures and may fall back to general
structure passages when no topic terms match.

### Optional plots in chat

Ask “Plot paracetamol solubility in DMSO + water” or open **Plot source data**
below a solubility answer. Switch between composition and temperature, then
download SVG or CSV. Charts parse retrieved Table 1 values directly, including
reported uncertainties and the mole-fraction × 100 scale. Supported systems are
paracetamol in DMSO/DMF/4FM + water and phenacetin in 4FM + water.
Other tables and qualitative coformer findings currently remain text answers.
Rendering runs in an isolated process with a timeout; failed plots leave chat
and sources available. Run `python checks/check_plots.py` to verify.
